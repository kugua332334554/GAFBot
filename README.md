[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
# GAFBot

GAFBot is a multi-functional bot designed for Telegram account sellers.Use python.

GAFBot是针对Telegram号商的多功能Bot。使用Python语言。

<img width="617" height="346" alt="image" src="https://github.com/user-attachments/assets/999a6cae-d787-46f1-ad7a-87d75a231169" />

# 管理员命令

/vip + id 添加VIP

/unvip + id 删除VIP

/gb 发送广播


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

# proxy.txt配置

必须选用 IPV4 出口的 HTTP 代理链接，格式为: IP:端口:账户:密码:过期时间戳

每行一个

# 合作商

<div align="center">
  <table border="0">
    <tr>
      <td align="center" bgcolor="#1a1a1a" style="border: 2px solid #d4af37; border-radius: 10px; padding: 20px;">
        <p><b>✨ 尊贵合作伙伴 ✨</b></p>
        <hr />
       <img width="320" height="320" alt="image" src="https://github.com/user-attachments/assets/bdca444d-d44a-4ba1-8692-7e0f69b6f8d7" />
        <p><font color="#d4af37" size="5"><b>MOBAI</b></font></p>
        <p><small>Telegram @mo13ai</small></p>
      </td>
    </tr>
  </table>
</div>
