import os
from flask import Flask, request, abort

from linebot.v3.messaging import (
    MessagingApi, 
    ApiClient, 
    Configuration, 
    ReplyMessageRequest, 
    TextMessage
)
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent, LocationMessageContent

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = ""
LINE_CHANNEL_SECRET = ""

# "Configuration -> ApiClient -> MessagingApi" 初始化流程
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration) 
messaging_api = MessagingApi(api_client) 

handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print(e)
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    if event.message.text == "check":
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="請傳送您的現在位置給我，我會幫你查附近停車場！")]
            )
        )
    else:
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="歡迎使用停車小狐狸！輸入『check』開始")] # 我稍微改了提示文字，讓使用者知道要輸入 check
            )
        )

@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location_message(event):
    latitude = event.message.latitude
    longitude = event.message.longitude
    messaging_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=f"你的位置座標：{latitude}, {longitude}。\n未來我會幫你查附近停車場！")]
        )
    )

if __name__ == "__main__":
    app.run(port=5000)