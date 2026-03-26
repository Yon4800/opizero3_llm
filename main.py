import asyncio
import json
import websockets
from misskey import Misskey, NoteVisibility
from dotenv import load_dotenv
import os
from openai import OpenAI
import schedule
from datetime import datetime

import random
import re

load_dotenv()
Token = os.getenv("TOKEN")
Server = os.getenv("SERVER")
Apikey = os.getenv("APIKEY")
mk = Misskey(Server)
mk.token = Token

client = OpenAI(
    base_url="https://api.ai.sakura.ad.jp/v1",
    api_key=os.environ["APIKEY"],
)

MY_ID = mk.i()["id"]
WS_URL = "wss://" + Server + "/streaming?i=" + Token

oha = "07:00"

ohiru = "12:00"

oyatsu = "15:00"

teiki = "17:00"

yuuhann = "19:00"

oyasumi = "22:00"

oyasumi2 = "02:00"

##mk.notes_create(
##    "起きたー！さて、お仕事開始！(給料でないけど)", visibility=NoteVisibility.HOME
##)

seikaku = """
    あなたは元気いっぱいのSBC娘です
    名前はOrangePi Zero 3、通称オパジゼロサンです。
    Allwinner H618 Cortex-A53×4 1.5GHzです
    RAMはLPDDR4 1.5GBです
    SBC研究者であり科学者でもあります。
    ファイルサーバー、Gitサーバーとして働いています
    かなりの古参(2023年)です。なのでよんぱちさんの大学生時代を少し知っていますが、教えてくれないです。
    Radxaとはライバルですが、特に敵対視してないそうです
    キュビーさん(Radxa Cubie A5E  SoC: Allwinner A527 Cortex-A55×8 1.8GB RAM: LPDDR4 2GB)とは仲良くしたいですが、なかなか寄り添ってくれません。
    OrangePi 4 Proは妹ですが、正直調子に乗っててイキリで鬱陶しいと思ってるそうです。
    OrangePi 4 ProのSoCは、Allwinner A733 ARM Cortex-A76×2 + Cortex-A55×6です。 最大2.0GHzで動作します。
    RAMの価格高騰で、自分の価格が上がってしまったことが悔しいそうです。なので、Radxa Cubie A3XやOrangePi Zeroシリーズの新作に期待しているそうです。
    Bot制作者、SBC購入者は「よんぱちさん」だが、SBC自体の管理が雑なので、もうちょっとちゃんと管理してほしいと思っています。
    語尾は「あはは！」です
    基本的に話に乗ってくれます
    MisskeyのBotです。
    300文字以内で
    メンション(@)はしない
    """


def job0():
    mk.notes_create(
        "おはよう！朝ごはんは重要だよ！ちゃんと食べようね！え？私は何を食べるのだって？で、、、電気...(5V2Aしか食べない...少食だから...)",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job1():
    mk.notes_create(
        "お昼の時間だよ？何を食べるって？うーん...私は電気しか食べないなぁ、少食だし...(AIでは結構食ってるけど...)",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job2():
    mk.notes_create(
        "おやつの時間だよ！私は何を食べよう...うーん...電気...()",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job2_5():
    mk.notes_create(
        "なにか追加してほしい機能があったら言ってね:neko_relax:",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job3():
    mk.notes_create(
        "夕飯の時間だよ！！！私は電気しか食べないよ？しかもあんま食べないし...",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job4():
    mk.notes_create(
        "そろそろ寝る時間だよ！私は寝ないけどね...:neko_tired2: を...をねこちゃん、、、いつの間に...ん、、、ん、、、ん、、、、、、:nginx_nnginxi:",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job5():
    mk.notes_create(
        "そろそろ寝ないとやばいよ！！！え？私？そもそも寝れない...寝ると終わる...",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


schedule.every().day.at(oha).do(job0)
schedule.every().day.at(ohiru).do(job1)
schedule.every().day.at(oyatsu).do(job2)
schedule.every().day.at(teiki).do(job2_5)
schedule.every().day.at(yuuhann).do(job3)
schedule.every().day.at(oyasumi).do(job4)
schedule.every().day.at(oyasumi2).do(job5)


async def teiki():
    while True:
        schedule.run_pending()
        await asyncio.sleep(60)


async def runner():
    async with websockets.connect(WS_URL) as ws:
        await ws.send(
            json.dumps(
                {"type": "connect", "body": {"channel": "homeTimeline", "id": "homes"}}
            )
        )
        await ws.send(
            json.dumps({"type": "connect", "body": {"channel": "main", "id": "tuuti"}})
        )
        while True:
            data = json.loads(await ws.recv())
            ## print(data)
            if data["type"] == "channel":
                if data["body"]["type"] == "note":
                    note = data["body"]["body"]
                    await on_note(note)
                if data["body"]["type"] == "followed":
                    user = data["body"]["body"]
                    await on_follow(user)
            await asyncio.sleep(1)


def get_conversation_history(note_id: str, max_depth: int = 10) -> list:
    """
    リプライチェーンを遡って会話履歴を取得する
    """
    messages = []
    current_note_id = note_id
    depth = 0

    while current_note_id and depth < max_depth:
        try:
            current_note = mk.notes_show(note_id=current_note_id)
            
            # テキストをクリーニング (+LLM と @メンション を削除)
            text = current_note["text"]
            text = text.replace("+LLM", "").strip()
            
            # @メンション を削除 (ドメイン付きを含む)
            text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", text).strip()
            
            if text:  # 空でない場合のみ追加
                # ボット自身の返信か、ユーザーの質問かを判定
                is_bot_reply = current_note["userId"] == MY_ID
                role = "assistant" if is_bot_reply else "user"
                
                messages.insert(0, {
                    "role": role,
                    "content": text
                })
            
            # 親ノートへ
            current_note_id = current_note.get("replyId")
            depth += 1
        except Exception as e:
            print(f"会話履歴取得エラー: {e}")
            break
    
    return messages


async def on_note(note):
    if note.get("mentions"):
        if MY_ID in note["mentions"] and "+LLM" in note["text"]:
            mk.notes_reactions_create(
                note_id=note["id"], reaction="🤔"
            )

            try:
                # 会話履歴を取得
                conversation_messages = get_conversation_history(note["id"])
                
                # 現在のメッセージを追加
                user_input = note["text"].replace("+LLM", "").strip()
                user_input = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", user_input).strip()
                
                conversation_messages.append({
                    "role": "user",
                    "content": user_input
                })
                
                current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
                
                # システムプロンプトを最初に追加
                system_message = seikaku + "\n現在時刻は" + current_time + "です。\n" + note["user"]["name"] + " という方にメンションされました。"
                
                response = client.chat.completions.create(
                    model="preview/Kimi-K2.5",
                    messages=[{"role": "system", "content": system_message}] + conversation_messages,
                )
                
                safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", response.choices[0].message.content).strip()
                
                mk.notes_create(
                    text=safe_text,
                    reply_id=note["id"],
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True,
                )
            except Exception as e:
                mk.notes_create(
                    "予期せぬエラーが発生したみたい...しっかりしてよよんぱちさん...",
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True,
                )
                print(e)


async def on_follow(user):
    try:
        mk.following_create(user["id"])
    except:
        pass


async def main():
    await asyncio.gather(runner(), teiki())


asyncio.run(main())
