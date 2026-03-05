[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
# GAFBot

GAFBot is a multi-functional bot designed for Telegram account sellers.Use python.

GAFBot是针对Telegram号商的多功能Bot。使用Python语言。


# 部署
1.上传源码

2.修改 .env

3.执行

# 安装依赖

pip install python-telegram-bot telethon python-dotenv flask requests opentele

pip install "python-telegram-bot[job-queue]"

# 运行机器人

python start.py


# 域名Nginx反代配置实例

```
server {
    listen 80;
    server_name 你的域名;
    
    location /getcode {
        proxy_pass http://127.0.0.1:7788;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```
