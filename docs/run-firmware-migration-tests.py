"""Run tests in isolated containers, using existing images and synthetic data only."""

from pathlib import Path
import argparse
import subprocess
import time
import uuid


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--functional", action="store_true", help="also test worker changes without MQTT")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    suffix = uuid.uuid4().hex[:12]
    database_name = "iot-firmware-test-db-" + suffix
    runner_name = "iot-firmware-test-runner-" + suffix
    # Inspect only: never exec tests in the active API or touch its environment.
    api_image = subprocess.check_output(
        ["docker", "inspect", "iot-platform-api", "--format", "{{.Image}}"], text=True
    ).strip()
    pg_image = subprocess.check_output(
        ["docker", "image", "inspect", "postgres:15", "--format", "{{.Id}}"], text=True
    ).strip()
    created = False
    test_image = "iot-functional-tests:" + suffix
    built = False
    try:
        if args.functional:
            # Separate temporary image; never replace any service image/tag.
            dockerfile = ("FROM iot-platform-api:latest\n"
                          "RUN pip install --no-cache-dir paho-mqtt==1.6.1 python-dotenv==1.0.1\n")
            # Ensure the tag still identifies the image inspected above.
            tagged = subprocess.check_output(
                ["docker", "image", "inspect", "iot-platform-api:latest", "--format", "{{.Id}}"], text=True
            ).strip()
            if tagged != api_image:
                raise RuntimeError("API image tag changed; refusing to build tests from a different base")
            run("docker", "build", "--tag", test_image, "-", input=dockerfile, text=True)
            built = True
        extra_mounts = []
        test_command = ["-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
        if args.functional:
            extra_mounts = ["--mount", f"type=bind,src={root / 'mqtt-worker'},dst=/worker,readonly",
                            "-e", "DATABASE_URL_WORKER=postgresql://postgres@127.0.0.1:5432/iot_firmware_migration_test"]
            test_command = ["-B", "-c",
                "import sys,unittest; sys.path.insert(0,'/worker'); "
                "suite=unittest.TestSuite([unittest.TestLoader().discover('/app/tests'),"
                "unittest.TestLoader().discover('/worker/tests')]); "
                "result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())"]
        run(
            "docker", "run", "--detach", "--name", database_name, "--network", "none",
            "--memory", "256m", "--cpus", "1", "--pids-limit", "128",
            "--tmpfs", "/var/lib/postgresql/data:rw,size=192m",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            "-e", "POSTGRES_DB=iot_firmware_migration_test", pg_image,
            stdout=subprocess.DEVNULL,
        )
        created = True
        for _ in range(40):
            ready = subprocess.run(
                ["docker", "exec", database_name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres",
                 "-d", "iot_firmware_migration_test"], capture_output=True
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Disposable PostgreSQL did not become ready")
        run(
            "docker", "run", "--rm", "--name", runner_name,
            "--network", "container:" + database_name, "--read-only",
            "--memory", "256m", "--cpus", "1", "--pids-limit", "128",
            "--tmpfs", "/tmp:rw,size=32m", "--mount",
            f"type=bind,src={root / 'api'},dst=/app,readonly",
            "--workdir", "/app", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "IOT_MIGRATION_TEST_DISPOSABLE=1",
            "-e", "DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5432/iot_firmware_migration_test",
            *extra_mounts,
            "--entrypoint", "python", test_image if args.functional else api_image,
            *test_command,
        )
    finally:
        if created:
            # UUID names refer only to resources created by this invocation.
            runner = subprocess.run(["docker", "inspect", runner_name], capture_output=True)
            if runner.returncode == 0:
                run("docker", "rm", "-f", runner_name, stdout=subprocess.DEVNULL)
            run("docker", "rm", "-f", "-v", database_name, stdout=subprocess.DEVNULL)
        if built:
            run("docker", "image", "rm", test_image, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
