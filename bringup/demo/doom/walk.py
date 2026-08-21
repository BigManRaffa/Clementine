import serial, sys, tty, termios, select

s = serial.Serial('/dev/ttyUSB0', 115200, timeout=0)
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
try:
    tty.setcbreak(fd)
    while True:
        r, _, _ = select.select([sys.stdin], [], [], 0.02)
        if r:
            c = sys.stdin.read(1)
            if c == 'q':
                break
            if c in 'wasd':
                s.write(c.encode())
        line = s.readline()
        if line:
            sys.stdout.write(line.decode(errors='ignore'))
            sys.stdout.flush()
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)