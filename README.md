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
# 合作商品

<div align="center">
  <table border="0">
    <tr>
      <td align="center" bgcolor="#1a1a1a" style="border: 2px solid #d4af37; border-radius: 10px; padding: 20px;">
        <p><b>✨ 尊贵合作伙伴 ✨</b></p>
        <hr />
        <img src="https://cdn5.telesco.pe/file/JjCEpdHdtXApXygCZSIxNb5hE79Iz1RqGJpvOMn4SjKMGWl0NB_Jecnl_ohdNSbla_w5OanHabmUymYrGa7F-cfZzTK2qGgyH-bCuHuKWvog03yzUIQsaNl3g_V9QzyYJE4DJyQWmsw315GvV7i2euk7IP6ZhASF_HPEYskUcBKQQq7Tj14CLumbdF0SdqfcTkw01p0A_80vQVpB6ipdVwvfsZURc8lWoT-akCZfcQ9yjZJCXCgsvq3qi3CtYuKl8I3fR3zRExIdsoI-hWSlOkICeeSB_fYIj3Wei4I7VP1W3e5aMaxqEBrs-8uBhQTe-u4GKfY4cuhc11a9kdFIZw.jpg" width="400">
        <p><font color="#d4af37" size="5"><b>ROYAL PREMIUM SPONSOR</b></font></p>
        <p><small>ESTABLISHED 2024</small></p>
      </td>
    </tr>
  </table>
</div>
