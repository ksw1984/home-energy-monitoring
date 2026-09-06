from sunspec2.modbus import client

device = client.SunSpecModbusClientDeviceTCP(
    slave_id=1,
    ipaddr="192.168.178.25",
    ipport=502,
)

try:
    device.scan()

    print("\n=== SunSpec models ===")

    for model_id, models in device.models.items():
        print(f"\n{model_id}")

        if not isinstance(models, list):
            models = [models]  # noqa: PLW2901

        for model in models:
            print(f"  {model}")

finally:
    device.close()
