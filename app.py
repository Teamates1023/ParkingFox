from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, LocationMessage

app = Flask(__name__)

# 這裡要換成你自己的 Channel Access Token 和 Channel Secret
LINE_CHANNEL_ACCESS_TOKEN = "你的Channel Access Token"
LINE_CHANNEL_SECRET = "3f96df993ada56c0ee57de6b4434b377"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
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

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    if event.message.text == "查詢":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請傳送您的現在位置給我，我會幫你查附近停車場！")
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="歡迎使用停車查查！輸入『查詢』開始")
        )

@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    latitude = event.message.latitude
    longitude = event.message.longitude
    # 這裡未來會換成查停車場
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"你的位置座標：{latitude}, {longitude}。\n未來我會幫你查附近停車場！")
    )

if __name__ == "__main__":
    app.run(port=5000)
