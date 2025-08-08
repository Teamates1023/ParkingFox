import os
import re
import requests
from urllib.parse import quote
from flask import Flask, request, abort

from linebot.v3.messaging import (
    MessagingApi, 
    ApiClient, 
    Configuration, 
    ReplyMessageRequest, 
    TextMessage,
    TemplateMessage, 
    ButtonsTemplate, 
    PostbackAction
)
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import (MessageEvent, TextMessageContent, PostbackEvent)

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = ""
LINE_CHANNEL_SECRET = ""

# "Configuration -> ApiClient -> MessagingApi" 初始化流程
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration) 
messaging_api = MessagingApi(api_client) 

handler = WebhookHandler(LINE_CHANNEL_SECRET)

user_state = {}

def query_parking_fees_taipei(plate: str, vehicle_type: str) -> str:
    """
    vehicle_type: 'C' 汽車, 'M' 機車
    規範：Result 可能為 null（表示無待繳），或帶 Bills/Reminders 等欄位（JSON） 
    """
    try:
        safe_plate = quote(plate)
        url = f"https://trafficapi.pma.gov.taipei/Parking/PayBill/CarID/{safe_plate}/CarType/{vehicle_type}"
        r = requests.get(url, timeout=2)  # 規範建議查詢逾時 <= 2 秒
        r.raise_for_status()
        data = r.json()

        status = data.get("Status")
        message = data.get("Message", "")
        result = data.get("Result")

        if status != "SUCCESS":
            # 規範的錯誤會用 Status=錯誤碼（如 ERR01/ERR02/ERR03），Message=描述
            return f"查詢失敗：{status or ''} {message or ''}".strip()

        # SUCCESS 且沒有待繳
        if result is None:
            return f"目前查無待繳停車費。\n車牌：{plate}\n車種：{'汽車' if vehicle_type=='C' else '機車'}"

        # 有待繳，整理欄位
        lines = [
            "【臺北市停車費查詢】",
            f"車牌：{result.get('CarID', plate)}",
            f"車種：{'汽車' if result.get('CarType')=='C' else '機車' if result.get('CarType')=='M' else (result.get('CarType') or vehicle_type)}",
            f"筆數：{result.get('TotalCount', 0)}  總金額：NT$ {result.get('TotalAmount', 0)}",
            ""
        ]

        # 停車單（未逾期/轉催繳中）
        bills = result.get("Bills", []) or []
        if bills:
            lines.append("— 停車單 —")
            for i, b in enumerate(bills[:10], 1):
                date = b.get("ParkingDate", "")
                limit = b.get("PayLimitDate", "")
                amt = b.get("PayAmount", b.get("Amount", 0))
                hours = b.get("ParkingHours", "")
                lines.append(f"{i}. {date}  截止:{limit}  時數:{hours}  應繳:NT$ {amt}")
            lines.append("")

        # 催繳單（逾期整合）
        reminders = result.get("Reminders", []) or []
        if reminders:
            lines.append("— 催繳單 —")
            for i, rm in enumerate(reminders[:10], 1):
                rno = rm.get("ReminderNo", "")
                rlimit = rm.get("ReminderLimitDate", "")
                rpay = rm.get("PayAmount", 0)
                extra = rm.get("ExtraCharge", 0)
                lines.append(f"{i}. 單號:{rno}  截止:{rlimit}  應繳:NT$ {rpay}（含工本費:{extra}）")
                # 如要展開催繳內含的各筆 Bill，可在此再列出
            lines.append("")

        # 附帶城市/機關/時間（如果回傳有）
        city = result.get("CityCode")
        
        footer_bits = []
        if city: footer_bits.append(f"City:{city}")        
        if footer_bits:
            lines.append(" / ".join(footer_bits))

        return "\n".join(lines)

    except requests.exceptions.Timeout:
        return "查詢逾時，請稍後再試。"
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "HTTP"
        return f"查詢失敗（{code}）。請稍後再試。"
    except ValueError:
        return "回傳格式非 JSON，可能為暫時性異常。"
    except Exception as e:
        return f"查詢時發生錯誤：{e}"


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

# 入口：查費
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    user_id = event.source.user_id
    text = event.message.text.strip()

    if text in ("查費", "查詢停車費", "查停車費"):
        user_state[user_id] = {"stage": "await_plate"}
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="請輸入車牌（可含英數/中文字/「-」，例如：ABC-1234 或 軍C-21110）")]
            )
        )
        return

    # 使用者輸入車牌
    state = user_state.get(user_id)
    if state and state.get("stage") == "await_plate":
        plate = text.upper()
        # 規範：英數/中文字/「-」；其他符號為格式錯誤（對應 ERR02）
        if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff\-]+", plate):
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="車牌格式不符規範，請再輸入一次（僅允許英數/中文/「-」）。")]
                )
            )
            return

        state["plate"] = plate
        state["stage"] = "await_type"

        # 讓使用者選 C/M（汽車/機車）
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TemplateMessage(
                        alt_text="請選擇車種",
                        template=ButtonsTemplate(
                            text=f"車牌：{plate}\n請選擇車種：",
                            actions=[
                                PostbackAction(label="汽車", data="type=C"),
                                PostbackAction(label="機車", data="type=M"),
                                PostbackAction(label="取消", data="type=cancel"),
                            ]
                        )
                    )
                ]
            )
        )
        return

    # 指引
    messaging_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text="輸入「查費」開始；或輸入車牌後依指示操作。")]
        )
    )

# 處理車種選擇
@handler.add(PostbackEvent)
def handle_postback(event: PostbackEvent):
    user_id = event.source.user_id
    data = event.postback.data or ""
    state = user_state.get(user_id)

    if data == "type=cancel":
        user_state.pop(user_id, None)
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="已取消本次查詢。")]
            )
        )
        return

    if state and state.get("stage") == "await_type" and data.startswith("type="):
        vehicle_type = data.split("=", 1)[1]  # C or M
        plate = state.get("plate")

        result_text = query_parking_fees_taipei(plate, vehicle_type)
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=result_text)]
            )
        )
        user_state.pop(user_id, None)
        return

    messaging_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text="請輸入「查費」重新開始。")]
        )
    )

if __name__ == "__main__":
    app.run(port=5000)


