import subprocess
import time
import signal
import sys
import os

processes = []

def signal_handler(sig, frame):
    print("\n正在关闭进程...")
    for p in processes:
        p.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def start_process(script):
    return subprocess.Popen(
        [sys.executable, script]
    )

if __name__ == '__main__':
    scripts = ['luyou.py', 'bot.py']
    
    for script in scripts:
        if os.path.exists(script):
            print(f"启动 {script}...")
            p = start_process(script)
            processes.append(p)
        else:
            print(f"文件不存在: {script}")
    
    while True:
        for i, p in enumerate(processes):
            if p.poll() is not None:
                script = scripts[i]
                print(f"{script} 异常退出，重启中...")
                processes[i] = start_process(script)
        time.sleep(5)
