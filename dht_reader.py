import os
import time
import random
import logging

logger = logging.getLogger(__name__)

# Load configuration from environment
# DHT_PIN: Physical pin number or BCM pin number depending on the library.
DHT_PIN = int(os.getenv("DHT_PIN", "4"))
DHT_TYPE = int(os.getenv("DHT_TYPE", "11"))


def _read_dht_opi_gpio(pin, sensor_type=11):
    """
    Reads DHT11/DHT22 sensor using OPi.GPIO with direct bit-bang protocol.
    For Orange Pi (Allwinner / non-Raspberry Pi SBC).
    Returns (temperature, humidity) or (None, None) on failure.
    """
    import OPi.GPIO as GPIO

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(pin, GPIO.OUT)

    # Send start signal: pull LOW for 18ms, then HIGH
    GPIO.output(pin, GPIO.LOW)
    time.sleep(0.018)
    GPIO.output(pin, GPIO.HIGH)
    time.sleep(0.00004)

    # Switch to input to receive data
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Wait for sensor response (LOW then HIGH)
    timeout = 0
    while GPIO.input(pin) == GPIO.HIGH:
        timeout += 1
        if timeout > 10000:
            return None, None
    timeout = 0
    while GPIO.input(pin) == GPIO.LOW:
        timeout += 1
        if timeout > 10000:
            return None, None
    timeout = 0
    while GPIO.input(pin) == GPIO.HIGH:
        timeout += 1
        if timeout > 10000:
            return None, None

    # Read 40 bits of data
    data = []
    for _ in range(40):
        # Wait for bit start (LOW pulse)
        timeout = 0
        while GPIO.input(pin) == GPIO.LOW:
            timeout += 1
            if timeout > 10000:
                return None, None

        # Measure HIGH pulse width to determine bit value
        # < 28us = 0, >= 28us = 1 (DHT11 spec)
        count = 0
        while GPIO.input(pin) == GPIO.HIGH:
            count += 1
            if count > 10000:
                return None, None
        data.append(1 if count > 16 else 0)

    GPIO.cleanup()

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
        logger.warning(f"DHT checksum error: expected {byte_data[4]}, got {checksum}")
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


def read_dht():
    """
    Reads temperature and humidity from DHT sensor.
    Tries multiple libraries. If all fail or are unavailable, returns mock values (when in development).
    Returns (temperature, humidity) or (None, None) if measurement fails.
    """
    # 1. Try Rockfruit_DHT (recommended for Radxa/Rock Pi)
    try:
        import Rockfruit_DHT as rockfruit_dht
        sensor = rockfruit_dht.DHT11 if DHT_TYPE == 11 else rockfruit_dht.DHT22
        # Rockfruit_DHT.read_retry returns (humidity, temperature)
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

    # 3. Try OPi.GPIO native bit-bang (Orange Pi / non-RPi SBCs)
    try:
        temp, hum = _read_dht_opi_gpio(DHT_PIN, DHT_TYPE)
        if temp is not None and hum is not None:
            return temp, hum
        else:
            logger.warning("OPi.GPIO bit-bang read returned None (sensor not connected or read error)")
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to read from OPi.GPIO bit-bang: {e}")

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

    # Fallback to mock value if running in non-SBC/Windows environment or if libraries are missing
    is_development = os.name == 'nt' or os.getenv("DHT_MOCK", "false").lower() == "true"
    if is_development:
        # Generate mock values
        # Temperature: 18.0 to 28.0, Humidity: 40.0 to 70.0
        mock_temp = random.uniform(18.0, 28.0)
        mock_hum = random.uniform(40.0, 70.0)
        logger.info(f"Using mock DHT data (Dev Mode): Temp={mock_temp:.1f}°C, Hum={mock_hum:.1f}%")
        return mock_temp, mock_hum

    logger.error("No DHT library available or reading failed on Linux environment.")
    return None, None
