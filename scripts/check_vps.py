import paramiko, sys

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('81.17.98.185', username='root', password='N1hat06082006', timeout=15)

cmd = (
    "echo '=== OS ===' && cat /etc/os-release | grep PRETTY_NAME; "
    "echo '=== RAM ===' && free -h | grep Mem; "
    "echo '=== CPU ===' && nproc; "
    "echo '=== DISK ===' && df -h / | tail -1; "
    "echo '=== DOCKER ===' && (docker --version 2>/dev/null || echo no docker); "
    "echo '=== COMPOSE ===' && (docker compose version 2>/dev/null || echo no compose); "
    "echo '=== CONTAINERS ===' && (docker ps 2>/dev/null || echo none); "
    "echo '=== HOME ===' && ls /root/"
)

stdin, stdout, stderr = client.exec_command(cmd)
print(stdout.read().decode())
err = stderr.read().decode()
if err.strip():
    print("STDERR:", err[:500], file=sys.stderr)
client.close()
