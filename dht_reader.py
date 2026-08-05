import os
import time
import struct
import mmap
import random
import logging

logger = logging.getLogger(__name__)

# Load configuration from environment
# DHT_PIN: Physical pin number on the 26-pin header (default: 7 = PC9 for OPi Zero 3)
DHT_PIN = int(os.getenv("DHT_PIN", "7"))
DHT_TYPE = int(os.getenv("DHT_TYPE", "11"))

# ---------------------------------------------------------------------------
# Orange Pi Zero 3 (Allwinner H618) GPIO register layout
# ---------------------------------------------------------------------------
_H618_GPIO_BASE = 0x0300B000
_PAGE_SIZE = 4096
_PORT_SIZE = 0x24   # bytes per port register block

# Offsets within each port block
_CFG0_OFF = 0x00    # pin function config for pins 0-7  (4 bits per pin)
_CFG1_OFF = 0x04    # pin function config for pins 8-15
_DATA_OFF = 0x10    # data register (read/write pin level)
_PUL0_OFF = 0x1C    # pull-up/down for pins 0-15

# Function codes written to CFG registers
_FUNC_INPUT  = 0x0
_FUNC_OUTPUT = 0x1

# Physical header pin → (port_index, pin_number)
# port_index: PA=0 PB=1 PC=2 PD=3 PE=4 PF=5 PG=6 PH=7
_BOARD_TO_PORT_PIN = {
    3:  (7, 5),   # PH5
    5:  (7, 4),   # PH4
    7:  (2, 9),   # PC9  ← default DHT11 data pin
    8:  (7, 2),   # PH2
    10: (7, 3),   # PH3
    11: (2, 11),  # PC11
    12: (2, 6),   # PC6
    13: (2, 5),   # PC5
    15: (2, 8),   # PC8
    16: (2, 15),  # PC15
    18: (2, 14),  # PC14
    19: (7, 7),   # PH7
    21: (7, 8),   # PH8
    22: (2, 7),   # PC7
    23: (7, 6),   # PH6
    24: (7, 9),   # PH9
    26: (2, 10),  # PC10
}

# SUNXI GPIO number (for sysfs fallback)
_OPI_ZERO3_BOARD_TO_SUNXI = {
    3: 229, 5: 228, 7: 73,  8: 226, 10: 227,
    11: 75, 12: 70, 13: 69, 15: 72, 16: 79,
    18: 78, 19: 231, 21: 232, 22: 71,
    23: 230, 24: 233, 26: 74,
}


def _get_sunxi_num(physical_pin):
    return _OPI_ZERO3_BOARD_TO_SUNXI.get(physical_pin, physical_pin)


# ---------------------------------------------------------------------------
# Method 1: Direct /dev/mem GPIO register access (best timing for DHT11)
# ---------------------------------------------------------------------------

def _read_dht_mmap(physical_pin, sensor_type=11):
    """
    DHT11/DHT22 reader using direct memory-mapped GPIO register access.
    For Allwinner H618 (Orange Pi Zero 3). Requires /dev/mem (root).
    Provides microsecond-level GPIO timing suitable for DHT protocol.
    Returns (temperature, humidity) or (None, None) on failure.
    """
    if physical_pin not in _BOARD_TO_PORT_PIN:
        raise ValueError(f"Physical pin {physical_pin} not in supported pin map")

    port_idx, pin_num = _BOARD_TO_PORT_PIN[physical_pin]
    port_base = _H618_GPIO_BASE + port_idx * _PORT_SIZE

    # Choose CFG register and bit shift based on pin number
    if pin_num < 8:
        cfg_rel_off = _CFG0_OFF
        cfg_shift = pin_num * 4
    else:
        cfg_rel_off = _CFG1_OFF
        cfg_shift = (pin_num - 8) * 4

    # mmap the page that contains this port's registers
    page_base = port_base & ~(_PAGE_SIZE - 1)
    page_off  = port_base & (_PAGE_SIZE - 1)

    with open('/dev/mem', 'rb+') as mem_file:
        gpio_map = mmap.mmap(
            mem_file.fileno(), _PAGE_SIZE,
            mmap.MAP_SHARED,
            mmap.PROT_READ | mmap.PROT_WRITE,
            offset=page_base,
        )

        cfg_addr  = page_off + cfg_rel_off
        data_addr = page_off + _DATA_OFF

        def _rr(addr):
            return struct.unpack_from('<I', gpio_map, addr)[0]

        def _wr(addr, val):
            struct.pack_into('<I', gpio_map, addr, val & 0xFFFFFFFF)

        def _set_output():
            v = _rr(cfg_addr)
            v = (v & ~(0xF << cfg_shift)) | (_FUNC_OUTPUT << cfg_shift)
            _wr(cfg_addr, v)

        def _set_input():
            v = _rr(cfg_addr)
            v &= ~(0xF << cfg_shift)   # 0x0 = input
            _wr(cfg_addr, v)

        def _pin_high():
            _wr(data_addr, _rr(data_addr) | (1 << pin_num))

        def _pin_low():
            _wr(data_addr, _rr(data_addr) & ~(1 << pin_num))

        def _read_pin():
            return (_rr(data_addr) >> pin_num) & 1

        TIMEOUT = 0.002  # 2 ms timeout per phase

        try:
            # --- Reset: hold HIGH to ensure sensor is in idle state ---
            _set_output()
            _pin_high()
            time.sleep(0.5)      # 500 ms HIGH reset pulse

            # --- DHT start signal ---
            _pin_low()
            time.sleep(0.020)    # 20 ms LOW (spec: min 18ms)
            _pin_high()
            time.sleep(0.000040) # 40 µs HIGH

            _set_input()

            # --- Wait for sensor acknowledgement ---
            t = time.perf_counter()
            while _read_pin() == 1:
                if time.perf_counter() - t > TIMEOUT:
                    logger.warning("DHT mmap: timeout waiting for ACK LOW")
                    return None, None
            t = time.perf_counter()
            while _read_pin() == 0:
                if time.perf_counter() - t > TIMEOUT:
                    logger.warning("DHT mmap: timeout waiting for ACK HIGH")
                    return None, None
            t = time.perf_counter()
            while _read_pin() == 1:
                if time.perf_counter() - t > TIMEOUT:
                    logger.warning("DHT mmap: timeout waiting for data start")
                    return None, None

            # --- Read 40 bits using perf_counter with fixed threshold ---
            # Measured distribution: '0' = 15-25µs, '1' = 60-70µs
            THRESHOLD_US = 40
            TIMEOUT_US = 500  # anything >500µs is a timeout (not a real bit)
            durations_us = []
            for bit_i in range(40):
                # Wait for bit LOW → HIGH transition
                t = time.perf_counter()
                while _read_pin() == 0:
                    if time.perf_counter() - t > TIMEOUT:
                        logger.warning(f"DHT mmap: timeout at bit {bit_i} LOW")
                        return None, None

                # Measure HIGH pulse duration
                t_high = time.perf_counter()
                while _read_pin() == 1:
                    if time.perf_counter() - t_high > TIMEOUT:
                        # Last 2 bits: sensor may release line
                        if bit_i >= 38:
                            break
                        logger.warning(f"DHT mmap: timeout at bit {bit_i} HIGH")
                        return None, None
                durations_us.append((time.perf_counter() - t_high) * 1_000_000)

            # Identify timed-out bits (>500µs) and normal bits
            timeout_indices = [i for i, d in enumerate(durations_us) if d > TIMEOUT_US]
            base_data = [1 if d > THRESHOLD_US else 0 for d in durations_us]

            # For timed-out bits, try all 0/1 combinations to find checksum match
            def _try_parse(bits, stype):
                byte_data = []
                for i in range(5):
                    b = 0
                    for bit in bits[i * 8:(i + 1) * 8]:
                        b = (b << 1) | bit
                    byte_data.append(b)
                chk = (byte_data[0] + byte_data[1] + byte_data[2] + byte_data[3]) & 0xFF
                if chk != byte_data[4]:
                    return None
                return byte_data

            if timeout_indices:
                # Try all combinations for timed-out bits
                found = None
                for combo in range(1 << len(timeout_indices)):
                    trial = base_data[:]
                    for j, idx in enumerate(timeout_indices):
                        trial[idx] = (combo >> j) & 1
                    result = _try_parse(trial, sensor_type)
                    if result is not None:
                        found = result
                        logger.info(
                            f"DHT resolved {len(timeout_indices)} timeout bit(s) "
                            f"at positions {timeout_indices}"
                        )
                        break
                if found is None:
                    logger.warning(
                        f"DHT mmap checksum error (all combos failed). "
                        f"Timeout bits: {timeout_indices}. "
                        f"Durations: {['%.0f' % d for d in durations_us]}"
                    )
                    return None, None
                byte_data = found
            else:
                byte_data = _try_parse(base_data, sensor_type)
                if byte_data is None:
                    logger.warning(
                        f"DHT mmap checksum error. "
                        f"Durations: {['%.0f' % d for d in durations_us]}"
                    )
                    return None, None

            if sensor_type == 11:
                temperature = float(byte_data[2])
                humidity    = float(byte_data[0])
            else:  # DHT22
                humidity    = ((byte_data[0] << 8) | byte_data[1]) / 10.0
                temperature = (((byte_data[2] & 0x7F) << 8) | byte_data[3]) / 10.0
                if byte_data[2] & 0x80:
                    temperature = -temperature

            logger.info(f"DHT read OK: temp={temperature}°C, hum={humidity}%, raw={byte_data}")
            return temperature, humidity

        finally:
            try:
                _set_input()
            except Exception:
                pass
            gpio_map.close()


# ---------------------------------------------------------------------------
# Method 2: Linux sysfs GPIO (fallback — timing less precise)
# ---------------------------------------------------------------------------

def _read_dht_sysfs(physical_pin, sensor_type=11):
    """
    DHT11/DHT22 reader via Linux sysfs GPIO.
    Less timing-accurate than mmap but works without knowing register addresses.
    Returns (temperature, humidity) or (None, None) on failure.
    """
    gpio_num      = _get_sunxi_num(physical_pin)
    gpio_path     = f"/sys/class/gpio/gpio{gpio_num}"
    export_path   = "/sys/class/gpio/export"
    unexport_path = "/sys/class/gpio/unexport"
    direction_path = f"{gpio_path}/direction"
    value_path     = f"{gpio_path}/value"

    if not os.path.exists(gpio_path):
        try:
            with open(export_path, 'w') as f:
                f.write(str(gpio_num))
            time.sleep(0.1)
        except PermissionError:
            raise PermissionError(
                f"Cannot export GPIO{gpio_num}. Run with sudo or add user to gpio group."
            )

    def _set_dir(d):
        with open(direction_path, 'w') as f:
            f.write(d)

    def _write(v):
        with open(value_path, 'w') as f:
            f.write('1' if v else '0')

    _vfd = None

    def _read():
        os.lseek(_vfd, 0, os.SEEK_SET)
        return int(os.read(_vfd, 1))

    TIMEOUT = 0.002

    try:
        _set_dir('out')
        _write(1)
        time.sleep(0.001)
        _write(0)
        time.sleep(0.018)
        _write(1)
        time.sleep(0.000040)

        _set_dir('in')
        _vfd = os.open(value_path, os.O_RDONLY)

        t = time.perf_counter()
        while _read() == 1:
            if time.perf_counter() - t > TIMEOUT: return None, None
        t = time.perf_counter()
        while _read() == 0:
            if time.perf_counter() - t > TIMEOUT: return None, None
        t = time.perf_counter()
        while _read() == 1:
            if time.perf_counter() - t > TIMEOUT: return None, None

        data = []
        for _ in range(40):
            t = time.perf_counter()
            while _read() == 0:
                if time.perf_counter() - t > TIMEOUT: return None, None
            t_high = time.perf_counter()
            while _read() == 1:
                if time.perf_counter() - t_high > TIMEOUT: return None, None
            duration_us = (time.perf_counter() - t_high) * 1_000_000
            data.append(1 if duration_us > 40 else 0)

        os.close(_vfd)
        _vfd = None

        byte_data = []
        for i in range(5):
            b = 0
            for bit in data[i * 8:(i + 1) * 8]:
                b = (b << 1) | bit
            byte_data.append(b)

        chk = (byte_data[0] + byte_data[1] + byte_data[2] + byte_data[3]) & 0xFF
        if chk != byte_data[4]:
            logger.warning(f"DHT sysfs checksum error. Raw: {byte_data}")
            return None, None

        if sensor_type == 11:
            return float(byte_data[2]), float(byte_data[0])
        else:
            hum  = ((byte_data[0] << 8) | byte_data[1]) / 10.0
            temp = (((byte_data[2] & 0x7F) << 8) | byte_data[3]) / 10.0
            if byte_data[2] & 0x80: temp = -temp
            return temp, hum

    finally:
        if _vfd is not None:
            try: os.close(_vfd)
            except Exception: pass
        try:
            with open(unexport_path, 'w') as f:
                f.write(str(gpio_num))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main read function — tries multiple methods in order
# ---------------------------------------------------------------------------

def read_dht():
    """
    Reads temperature and humidity from DHT sensor.
    Tries multiple methods/libraries in order of reliability.
    Returns (temperature, humidity) or (None, None) if all methods fail.
    """
    sunxi_num = _get_sunxi_num(DHT_PIN)

    # 1. Try Rockfruit_DHT (Radxa/Rock Pi)
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

    # 3. Direct /dev/mem GPIO register access (Orange Pi Zero 3 / H618, best timing)
    try:
        for attempt in range(5):
            temp, hum = _read_dht_mmap(DHT_PIN, DHT_TYPE)
            if temp is not None and hum is not None:
                return temp, hum
            time.sleep(2)  # DHT11 spec: min 2 seconds between reads
        logger.warning(
            f"mmap DHT read returned None after 3 attempts "
            f"(physical_pin={DHT_PIN}, gpio=GPIO{sunxi_num}). "
            "Check sensor wiring. If using a bare DHT11 (not a module board), "
            "a 4.7k-10k pull-up resistor is required between VCC and DATA."
        )
    except PermissionError as e:
        logger.error(f"mmap GPIO permission error: {e}")
    except ValueError as e:
        logger.warning(f"mmap GPIO pin mapping error: {e}")
    except Exception as e:
        logger.warning(f"Failed to read from mmap GPIO (pin={DHT_PIN}): {e!r}")

    # 4. sysfs GPIO fallback (less precise timing)
    try:
        for attempt in range(3):
            temp, hum = _read_dht_sysfs(DHT_PIN, DHT_TYPE)
            if temp is not None and hum is not None:
                return temp, hum
            time.sleep(1)
        logger.warning(
            f"sysfs DHT read returned None after 3 attempts "
            f"(physical_pin={DHT_PIN}, gpio=GPIO{sunxi_num})."
        )
    except PermissionError as e:
        logger.error(f"sysfs GPIO permission error: {e}")
    except Exception as e:
        logger.warning(f"Failed to read from sysfs GPIO (pin={DHT_PIN}): {e!r}")

    # 5. dht11 library (Raspberry Pi only — last resort)
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

    # 6. pigpio_dht
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
        mock_hum  = random.uniform(40.0, 70.0)
        logger.info(f"Using mock DHT data (Dev Mode): Temp={mock_temp:.1f}°C, Hum={mock_hum:.1f}%")
        return mock_temp, mock_hum

    logger.error("No DHT library available or reading failed on Linux environment.")
    return None, None
