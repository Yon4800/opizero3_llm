import os
import time
import random
import logging

logger = logging.getLogger(__name__)

# Load configuration from environment
# DHT_PIN: Physical pin number on the 26-pin header (default: 7 = PC9 for OPi Zero 3)
DHT_PIN = int(os.getenv("DHT_PIN", "7"))
DHT_TYPE = int(os.getenv("DHT_TYPE", "11"))
# DHT_GPIO_MODE: "BOARD" (physical pin number, default) or "SUNXI" (SoC GPIO number)
DHT_GPIO_MODE = os.getenv("DHT_GPIO_MODE", "BOARD")

# Orange Pi Zero 3 physical pin → Allwinner H618 SUNXI GPIO number
# Formula: PC_n = 64 + n, PH_n = 224 + n
_OPI_ZERO3_BOARD_TO_SUNXI = {
    3:  229,  # PH5
    5:  228,  # PH4
    7:  73,   # PC9  ← recommended for DHT11
    8:  226,  # PH2
    10: 227,  # PH3
    11: 75,   # PC11
    12: 70,   # PC6
    13: 69,   # PC5
    15: 72,   # PC8
    16: 79,   # PC15
    18: 78,   # PC14
    19: 231,  # PH7
    21: 232,  # PH8
    22: 71,   # PC7
    23: 230,  # PH6
    24: 233,  # PH9
    26: 74,   # PC10
}


def _get_sunxi_num(physical_pin):
    """Convert physical pin number to SUNXI (SoC) GPIO number for Orange Pi Zero 3."""
    return _OPI_ZERO3_BOARD_TO_SUNXI.get(physical_pin, physical_pin)


def _read_dht_sysfs(physical_pin, sensor_type=11):
    """
    Reads DHT11/DHT22 via Linux sysfs GPIO interface.
    Works on any Linux SBC (Orange Pi, Rock Pi, etc.) without board-specific libraries.
    Requires read/write access to /sys/class/gpio (run with sudo or add user to gpio group).
    Returns (temperature, humidity) or (None, None) on failure.
    """
    gpio_num = _get_sunxi_num(physical_pin)
    gpio_path = f"/sys/class/gpio/gpio{gpio_num}"
    export_path = "/sys/class/gpio/export"
    unexport_path = "/sys/class/gpio/unexport"
    direction_path = f"{gpio_path}/direction"
    value_path = f"{gpio_path}/value"

    # Export GPIO if not already exported
    if not os.path.exists(gpio_path):
        try:
            with open(export_path, 'w') as f:
                f.write(str(gpio_num))
            time.sleep(0.1)
        except PermissionError:
            raise PermissionError(
                f"Cannot export GPIO{gpio_num}. "
                "Run with sudo, or add user to gpio group: sudo usermod -aG gpio $USER"
            )

    def _set_dir(direction):
        with open(direction_path, 'w') as f:
            f.write(direction)

    def _write(val):
        with open(value_path, 'w') as f:
            f.write('1' if val else '0')

    # Pre-open value file for fast reading
    _vfd = None
    def _read():
        os.lseek(_vfd, 0, os.SEEK_SET)
        return int(os.read(_vfd, 1))

    try:
        # Send DHT start signal: HIGH → LOW 18ms → HIGH 40us
        _set_dir('out')
        _write(1)
        time.sleep(0.001)
        _write(0)
        time.sleep(0.018)   # 18ms LOW
        _write(1)
        time.sleep(0.00004) # 40us HIGH

        # Switch to input for data reading
        _set_dir('in')
        _vfd = os.open(value_path, os.O_RDONLY)

        # Wait for sensor acknowledgement (LOW then HIGH then LOW)
        t = time.perf_counter()
        while _read() == 1:
            if time.perf_counter() - t > 0.001:
                return None, None  # timeout
        t = time.perf_counter()
        while _read() == 0:
            if time.perf_counter() - t > 0.001:
                return None, None
        t = time.perf_counter()
        while _read() == 1:
            if time.perf_counter() - t > 0.001:
                return None, None

        # Read 40 bits: each bit starts with ~50us LOW, then HIGH
        # HIGH < 28us = 0, HIGH >= 28us = 1 (DHT11 spec)
        data = []
        for _ in range(40):
            # Wait for LOW → HIGH transition (bit start)
            t = time.perf_counter()
            while _read() == 0:
                if time.perf_counter() - t > 0.001:
                    return None, None

            # Measure HIGH pulse duration
            t_high = time.perf_counter()
            while _read() == 1:
                if time.perf_counter() - t_high > 0.001:
                    return None, None
            duration_ns = (time.perf_counter() - t_high) * 1e9

            # > 40us HIGH = bit 1, < 40us HIGH = bit 0
            data.append(1 if duration_ns > 40000 else 0)

        os.close(_vfd)
        _vfd = None

        # Parse 5 bytes from 40 bits
        byte_data = []
        for i in range(5):
            byte_val = 0
            for bit in data[i * 8:(i + 1) * 8]:
                byte_val = (byte_val << 1) | bit
            byte_data.append(byte_val)

        # Verify checksum
        checksum = (byte_data[0] + byte_data[1] + byte_data[2] + byte_data[3]) & 0xFF
        if checksum != byte_data[4]:
            logger.warning(
                f"DHT sysfs checksum error: expected {byte_data[4]}, "
                f"got {checksum}. Raw: {byte_data}"
            )
            return None, None

        if sensor_type == 11:
            humidity = float(byte_data[0])
            temperature = float(byte_data[2])
        else:
            # DHT22
            humidity = ((byte_data[0] << 8) | byte_data[1]) / 10.0
            temperature = (((byte_data[2] & 0x7F) << 8) | byte_data[3]) / 10.0
            if byte_data[2] & 0x80:
                temperature = -temperature

        return temperature, humidity

    finally:
        if _vfd is not None:
            try:
                os.close(_vfd)
            except Exception:
                pass
        # Unexport GPIO to clean up
        try:
            with open(unexport_path, 'w') as f:
                f.write(str(gpio_num))
        except Exception:
            pass


def read_dht():
    """
    Reads temperature and humidity from DHT sensor.
    Tries multiple libraries in order. Falls back to mock values in dev/Windows env.
    Returns (temperature, humidity) or (None, None) if measurement fails.
    """
    # 1. Try Rockfruit_DHT (recommended for Radxa/Rock Pi)
    try:
        import Rockfruit_DHT as rockfruit_dht
        sensor = rockfruit_dht.DHT11 if DHT_TYPE == 11 else rockfruit_dht.DHT22
        humidity, temperature = rockfruit_dht.read_retry(sensor, DHT_PIN)
        if temperature is not None and humidity is not None:
            return temperature, humidity
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to read from Rockfruit_DHT: {e}")

    # 2. Try Adafruit_DHT
    try:
        import Adafruit_DHT as adafruit_dht
        sensor = adafruit_dht.DHT11 if DHT_TYPE == 11 else adafruit_dht.DHT22
        humidity, temperature = adafruit_dht.read_retry(sensor, DHT_PIN)
        if temperature is not None and humidity is not None:
            return temperature, humidity
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to read from Adafruit_DHT: {e}")

    # 3. Try Linux sysfs GPIO bit-bang (Orange Pi Zero 3 and other non-RPi SBCs)
    #    Requires sudo or gpio group membership.
    #    DHT_PIN must be the physical pin number on the 26-pin header.
    sunxi_num = _get_sunxi_num(DHT_PIN)
    try:
        for attempt in range(3):
            temp, hum = _read_dht_sysfs(DHT_PIN, DHT_TYPE)
            if temp is not None and hum is not None:
                return temp, hum
            time.sleep(1)
        logger.warning(
            f"sysfs DHT read returned None after 3 attempts "
            f"(physical_pin={DHT_PIN}, gpio=GPIO{sunxi_num}). "
            "Check sensor wiring and pin number."
        )
    except PermissionError as e:
        logger.error(f"sysfs GPIO permission error: {e}")
    except Exception as e:
        logger.warning(f"Failed to read from sysfs GPIO (pin={DHT_PIN}, gpio={sunxi_num}): {e!r}")

    # 4. Try dht11 (szazo/DHT11_Python) - Raspberry Pi only, kept as last resort
    try:
        import dht11
        try:
            import RPi.GPIO as GPIO
        except ImportError:
            import OPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        instance = dht11.DHT11(pin=DHT_PIN)
        result = instance.read()
        if result.is_valid():
            return result.temperature, result.humidity
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to read from dht11 (szazo): {e}")

    # 5. Try pigpio_dht
    try:
        from pigpio_dht import DHT11, DHT22
        sensor = DHT11(gpio=DHT_PIN) if DHT_TYPE == 11 else DHT22(gpio=DHT_PIN)
        result = sensor.read()
        if result.get('valid'):
            return result['temp_c'], result['humidity']
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to read from pigpio_dht: {e}")

    # Fallback: mock values for Windows / dev environment
    is_development = os.name == 'nt' or os.getenv("DHT_MOCK", "false").lower() == "true"
    if is_development:
        mock_temp = random.uniform(18.0, 28.0)
        mock_hum = random.uniform(40.0, 70.0)
        logger.info(f"Using mock DHT data (Dev Mode): Temp={mock_temp:.1f}°C, Hum={mock_hum:.1f}%")
        return mock_temp, mock_hum

    logger.error("No DHT library available or reading failed on Linux environment.")
    return None, None
