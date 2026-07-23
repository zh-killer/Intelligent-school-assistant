#!/usr/bin/python3

import smtplib
from email.mime.text import MIMEText
from email.header import Header

# 第三方 SMTP 服务
mail_host = "smtp.qq.com"  # 设置服务器
mail_user = "3773353416@qq.com"  # 用户名
mail_pass = "qfraslbimnbucgee"  # 口令

sender = '3773353416@qq.com'
receivers = ['2419819951@qq.com']  # 接收邮件，可设置为你的QQ邮箱或者其他邮箱

message = MIMEText('敢回答大头照给你开了', 'plain', 'utf-8')
message['From'] =sender
message['To'] = receivers[0]

subject = '张元英柳智敏谁是五女一？'
message['Subject'] =subject

try:
    smtpObj = smtplib.SMTP_SSL(mail_host, 465)
    smtpObj.login(mail_user, mail_pass)
    smtpObj.sendmail(sender, receivers, message.as_string())
    print("邮件发送成功")
except smtplib.SMTPException as e:
    print("Error: 无法发送邮件")