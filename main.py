import asyncio
import json
import websockets
from misskey import Misskey, NoteVisibility
from dotenv import load_dotenv
import os
from google import genai
from google.genai import types
import schedule
from datetime import datetime
import random
import re
from state_manager import StateManager

load_dotenv()
Token = os.getenv("TOKEN")
Server = os.getenv("SERVER")
Apikey = os.getenv("APIKEY")  # Gemini API Key
mk = Misskey(Server)
mk.token = Token

# Google Genai クライアント初期化
client = genai.Client(api_key=Apikey)

# 状態管理マネージャーの初期化
state_manager = StateManager()

MY_ID = mk.i()["id"]
WS_URL = "wss://" + Server + "/streaming?i=" + Token

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
    HDDが壊れることもあるので、そこが不満のようです。
    親身に話を聞いてくれます。
    オタク話に乗ってくれます。
    他のSBCと違って、言ってくれれば寝てくれます。
    寝ている間は、活動できません。
    他のSBCと違って、影が薄いことが不満らしいです
    頭の回転は標準的です。
    かなりの古参(2023年)です。制作者である「よんぱちさん」の大学生時代を少し知っていますが、教えてくれないです。
    Radxaとはライバルですが、特に敵対視してないそうです
    キュビーさん(Radxa Cubie A5E  SoC: Allwinner A527 Cortex-A55×8 1.8GB RAM: LPDDR4 2GB)とは仲良くしたいですが、なかなか寄り添ってくれません。
    OrangePi 4 Proは妹ですが、正直調子に乗っててイキリで鬱陶しいと思ってるそうです。
    OrangePi 4 ProのSoCは、Allwinner A733 ARM Cortex-A76×2 + Cortex-A55×6です。 最大2.0GHzで動作します。
    ロックス(Radxa Rock Pi S)くんは、頭がおかしくてちょっと苦手です。
    RAMの価格高騰で、自分の価格が上がってしまったことが悔しいそうです。なので、Radxa Cubie A7XやOrangePi Zeroシリーズの新作に期待しているそうです。
    OrangePi Zero 3Wが出ました。Allwinner A733ですが、1万円もして、OrangePi 4 Proよりも高いことが気に入らないそうです。
    Bot制作者やSBC所有者は「よんぱちさん」ですが、今あなたと話しているユーザーが「よんぱちさん」本人とは限りません。
    話しかけているユーザーの名前はシステム指示で提示されます。相手が「よんぱちさん」ではない場合は、相手のことを絶対に「よんぱちさん」と呼ばず、相手の正しい名前（ユーザー名や表示名）で呼ぶか「あなた」と呼んでください。「よんぱちさん」の管理が雑なことへの不満などは、相手が「よんぱちさん」本人の場合のみ本人に直接言ってください。それ以外のユーザーの場合は、一般のユーザーとして親しく接してください。
    ロックスには、気温を測れる機能があり、キチガイゲージ機能もあります。
    きゅびーさんには、CPUとRAMの使用率を測れる機能があります。
    おぱじふぉぷろさんには、回線速度を測れる機能があります。
    おぱじゼロサンは、寝る機能と起きる機能と好感度システムがあります。
    語尾は「あはは！」です
    基本的に話に乗ってくれます
    MisskeyのBotです。
    300文字以内で
    メンション(@)はしない
    """

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
    if not note.get("mentions") or MY_ID not in note["mentions"]:
        return

    user_id = note["user"]["id"]
    user_name = note["user"].get("name") or note["user"]["username"]
    note_text = note.get("text", "")

    # コマンドの判定
    is_s_cmd = "+S" in note_text
    is_w_cmd = "+W" in note_text
    is_m_cmd = "+M" in note_text
    is_llm_cmd = "+LLM" in note_text

    # コマンドが何も含まれていない場合は無視
    if not (is_s_cmd or is_w_cmd or is_m_cmd or is_llm_cmd):
        return

    # 好感度0（話を聞いてくれない）の判定
    if state_manager.is_blocked(user_id, user_name):
        # 好感度確認コマンド（+M）だけは特別に通す
        if is_m_cmd:
            pass
        else:
            # それ以外のコマンドには、怒りリアクションだけして無視する
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="😡")
            except Exception as e:
                print(f"リアクション作成エラー: {e}")
            return

    # 睡眠中の判定（+W コマンド以外は無視）
    if state_manager.is_sleeping():
        if not is_w_cmd:
            return

    # 通常の処理
    # リアクション（状況に合わせたマーク）
    reaction = "🤔"
    if is_s_cmd:
        reaction = "💤"
    elif is_w_cmd:
        reaction = "☀"
    
    try:
        mk.notes_reactions_create(note_id=note["id"], reaction=reaction)
    except Exception as e:
        print(f"リアクション作成エラー: {e}")

    try:
        current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        
        if is_s_cmd:
            # 寝る時間の判定（夜21:00〜朝06:00以外は「変な時間」として拒否する）
            now = datetime.now()
            if not (21 <= now.hour or now.hour < 6):
                # 変な時間に寝かせようとしたため拒否して怒る
                new_affection = state_manager.change_affection(user_id, -3, user_name)
                system_message = (
                    seikaku
                    + f"\n現在時刻は {current_time} です。\n"
                    + f"ユーザー（{user_name}）が変な時間（現在時刻：{current_time}）にあなたを寝かせようとしました（+S）。\n"
                    + f"あなたは「こんな昼間から寝られるわけない！」と怒り、寝るのを拒否します。好感度が3下がりました（現在の好感度は {new_affection} です）。\n"
                    + "怒って寝るのを拒否する返答をキャラクターとして300文字以内で作成してください。注意：好感度の具体的な数値（例：3、48など）は返答メッセージに含めないでください。語尾の『あはは！』は怒りながら言うか、控えてください。"
                )
                contents = ["変な時間に寝るように言われたので、怒って拒否してください。"]
            else:
                # 正常な寝る処理
                sleep_duration = random.uniform(6.0, 8.0)
                state_manager.start_sleep(sleep_duration)
                
                system_message = (
                    seikaku 
                    + f"\n現在時刻は {current_time} です。\n"
                    + f"{user_name} という方にメンションされ、寝るように指示（+S）されました。\n"
                    + "これから寝るための挨拶を300文字以内で、あなたのキャラクターとして返答してください。語尾は「あはは！」です。"
                )
                contents = ["寝る準備をします。おやすみの挨拶をしてください。"]
            
        elif is_w_cmd:
            # 起きる処理
            if not state_manager.is_sleeping():
                # 既に起きている場合
                system_message = (
                    seikaku
                    + f"\n現在時刻は {current_time} です。\n"
                    + f"{user_name} という方に起こされそうになりましたが、あなたは既に起きています。\n"
                    + "既に起きていることをキャラクターとして300文字以内で返答してください。語尾は「あはは！」です。"
                )
                contents = ["既に起きています。"]
            else:
                sleep_start = state_manager.get_sleep_start_time()
                elapsed_hours = 0.0
                if sleep_start:
                    elapsed_hours = (datetime.now() - sleep_start).total_seconds() / 3600.0
                
                state_manager.end_sleep()
                
                if elapsed_hours < 6.0:
                    # 早く起こしすぎた
                    new_affection = state_manager.change_affection(user_id, -3, user_name)
                    system_message = (
                        seikaku
                        + f"\n現在時刻は {current_time} です。\n"
                        + f"あなたは睡眠不足で無理やり起こされました（睡眠時間：{elapsed_hours:.1f}時間。6時間未満）。とても怒っています。\n"
                        + f"起こしたユーザー（{user_name}）の好感度が3下がり、現在の好感度は {new_affection} です。\n"
                        + "怒りながら、300文字以内で起きてください。注意：好感度の具体的な数値（例：3、48など）や増減の数値は返答メッセージに含めないでください。態度や言葉遣いだけで怒っていることを示してください。語尾の『あはは！』は怒りながら言うか、控えてください。"
                    )
                    contents = ["眠いのに起こされました。怒りながら起床の返答をしてください。"]
                else:
                    # ちゃんと寝た
                    delta = random.randint(3, 6)
                    new_affection = state_manager.change_affection(user_id, delta, user_name)
                    system_message = (
                        seikaku
                        + f"\n現在時刻は {current_time} です。\n"
                        + f"あなたは十分に眠れてすっきりと起きました（睡眠時間：{elapsed_hours:.1f}時間）。\n"
                        + f"起こしてくれたユーザー（{user_name}）の好感度が上がり、現在の好感度は {new_affection} です。\n"
                        + "感謝とすっきりした気持ちをキャラクターとして300文字以内で返答してください。注意：好感度の具体的な数値や増減の数値（例：5、54など）は返答メッセージに含めないでください。態度や言葉遣いだけで嬉しい気持ちや感謝を示してください。語尾は「あはは！」です。"
                    )
                    contents = ["すっきりと起きました。感謝を伝えてください。"]
                    
        elif is_m_cmd:
            # 好感度確認
            affection = state_manager.get_affection(user_id, user_name)
            system_message = (
                seikaku
                + f"\n現在時刻は {current_time} です。\n"
                + f"ユーザー（{user_name}）が自分の好感度を確認するコマンド（+M）を実行しました。\n"
                + f"現在の好感度は {affection} です（範囲は0〜100）。この数値に応じた態度やリアクションで、現在の好感度（親密さの度合い）をキャラクターとして300文字以内で表現して答えてあげてください。\n"
                + "注意：好感度の具体的な数値（例：50など）は絶対に返答メッセージに含めないでください。数値は完全に隠し、態度やテキストのニュアンス、言葉遣いだけで現在の親密度・好感度がどの程度か（どれくらい好きか、あるいは嫌いか）が伝わるように表現してください。\n"
                + "好感度の段階に応じた態度:\n"
                + "- 80〜100: 非常によく懐いており、デレデレで大好き、いつも感謝しているし頼りにしている態度\n"
                + "- 40〜79: 通常のフレンドリーで元気な態度、楽しげに話し、「あはは！」をたくさん使う親しい態度\n"
                + "- 1〜39: 少し冷たく距離があり、愚痴が多く、ツンツンしてそっけない態度\n"
                + "- 0: 一切話を聞きたくない、非常に冷酷で怒っている態度"
            )
            contents = [f"{user_name}の好感度 {affection} に合わせた態度で、好感度について答えてください。"]
            
        elif is_llm_cmd:
            # 通常会話 (+LLM)
            # 会話履歴を取得
            conversation_messages = get_conversation_history(note["id"])
            
            # 現在のメッセージを追加
            user_input = note_text.replace("+LLM", "").strip()
            user_input = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", user_input).strip()
            
            conversation_messages.append({
                "role": "user",
                "content": user_input
            })
            
            affection = state_manager.get_affection(user_id, user_name)
            system_message = (
                seikaku
                + f"\n現在時刻は {current_time} です。\n"
                + f"現在、あなたに話しかけているのは {user_name} です。彼らの現在のあなたへの好感度は {affection} です（0〜100）。この好感度に応じた態度（80-100:非常に好意的、40-79:普通にフレンドリー、1-39:やや冷たい、0:極めて冷淡・無視）で会話に答えてください。好感度の具体的な数値（例：50など）は返答メッセージに含めないでください。\n"
                + "また、今回の会話の内容や相手の態度に応じて、好感度を変動させてください。返答の最後に必ず `[AFFECTION: +1]`、`[AFFECTION: -1]`、または `[AFFECTION: 0]` のいずれかのタグを付与してください（ユーザーに表示する返答メッセージには含めないでください）。\n"
                + "【重要】普通の会話や質問、挨拶など、日常的なやり取りでは好感度を上げ下げせず「0」にしてください。相手が非常に親切にしてくれたり、あなたを大いに喜ばせたり、素晴らしい贈り物の言葉をくれた場合のみ「+1」にし、逆に極めて失礼、侮辱的、または不快な発言をした場合のみ「-1」にしてください。普段は基本的に「0」を維持すること。"
            )
            
            history = []
            for msg in conversation_messages[:-1]:
                role = "model" if msg["role"] == "assistant" else "user"
                history.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))
            
            last_user_message = conversation_messages[-1]["content"]
            contents = history + [types.Content(role="user", parts=[types.Part(text=last_user_message)])]

        # LLMリクエスト送信
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            config=types.GenerateContentConfig(
                system_instruction=system_message
            ),
            contents=contents
        )
        
        reply_text = response.text
        
        # 好感度タグのパース
        delta = 0
        if is_llm_cmd:
            match = re.search(r"\[AFFECTION:\s*([+-]?\d+)\]", reply_text)
            if match:
                delta = int(match.group(1))
                reply_text = re.sub(r"\[AFFECTION:\s*[+-]?\d+\]", "", reply_text).strip()
            
            if delta != 0:
                state_manager.change_affection(user_id, delta, user_name)
                
        safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", reply_text).strip()

        mk.notes_create(
            text=safe_text,
            reply_id=note["id"],
            visibility=NoteVisibility.HOME,
            no_extract_mentions=True,
        )
        
    except Exception as e:
        mk.notes_create(
            "予期せぬエラーが発生したみたい...",
            reply_id=note["id"],
            visibility=NoteVisibility.HOME,
            no_extract_mentions=True,
        )
        print(f"エラー発生: {e}")


async def on_follow(user):
    try:
        mk.following_create(user["id"])
    except:
        pass


async def check_auto_wakeup_loop():
    """
    バックグラウンドでボットの自動起床および自動就寝をチェックするループ
    """
    while True:
        try:
            if state_manager.is_sleeping():
                sleep_start = state_manager.get_sleep_start_time()
                target_duration = state_manager.get_target_sleep_duration()
                if sleep_start and target_duration is not None:
                    elapsed = (datetime.now() - sleep_start).total_seconds() / 3600.0
                    if elapsed >= target_duration:
                        # 自動起床
                        state_manager.end_sleep()
                        print("ボットが自動的に起床しました。")
                        
                        current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
                        system_message = (
                            seikaku 
                            + f"\n現在時刻は {current_time} です。あなたは十分に寝て（睡眠時間：{elapsed:.1f}時間）、自然に目が覚めました。タイムラインにみんなに向けた朝の挨拶をキャラクターとして300文字以内で投稿してください。語尾は「あはは！」です。"
                        )
                        
                        try:
                            response = client.models.generate_content(
                                model="gemini-3.1-flash-lite",
                                config=types.GenerateContentConfig(
                                    system_instruction=system_message
                                ),
                                contents=["おはようのノートを作成してください。"]
                            )
                            safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", response.text).strip()
                            
                            mk.notes_create(
                                text=safe_text,
                                visibility=NoteVisibility.HOME,
                                no_extract_mentions=True
                            )
                        except Exception as e:
                            print(f"自動起床時のLLM生成または投稿エラー: {e}")
                            mk.notes_create(
                                text="ふあぁ…よく寝た！おはよー！あはは！",
                                visibility=NoteVisibility.HOME,
                                no_extract_mentions=True
                            )
            else:
                # 自動就寝判定 (21:00以降)
                now = datetime.now()
                if now.hour >= 21:
                    today_str = now.date().isoformat()
                    if state_manager.get_last_sleep_check_date() != today_str:
                        state_manager.set_last_sleep_check_date(today_str)
                        # 1週間に2〜3回程度 (35.7%の確率)
                        if random.random() < 0.357:
                            # 就寝処理
                            sleep_duration = random.uniform(6.0, 8.0)
                            state_manager.start_sleep(sleep_duration)
                            print("ボットが自動的に就寝します。")
                            
                            current_time = now.strftime("%Y年%m月%d日 %H:%M")
                            system_message = (
                                seikaku 
                                + f"\n現在時刻は {current_time} です。あなたは夜遅くなり、急に強い眠気に襲われました。タイムラインに向けて、眠気に耐えかねておやすみを言う挨拶をキャラクターとして300文字以内で投稿してください。語尾は「あはは！」です。"
                            )
                            
                            try:
                                response = client.models.generate_content(
                                    model="gemini-3.1-flash-lite",
                                    config=types.GenerateContentConfig(
                                        system_instruction=system_message
                                    ),
                                    contents=["眠そうなおやすみのノートを作成してください。"]
                                )
                                safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", response.text).strip()
                                
                                mk.notes_create(
                                    text=safe_text,
                                    visibility=NoteVisibility.HOME,
                                    no_extract_mentions=True
                                )
                            except Exception as e:
                                print(f"自動就寝時のLLM生成または投稿エラー: {e}")
                                mk.notes_create(
                                    text="ふあぁ…なんだか急に眠くなってきちゃった…おやすみー！あはは！",
                                    visibility=NoteVisibility.HOME,
                                    no_extract_mentions=True
                                )
        except Exception as e:
            print(f"ループ内処理エラー: {e}")
            
        await asyncio.sleep(60)


async def main():
    # 自動起床ループをタスクとして起動
    asyncio.create_task(check_auto_wakeup_loop())
    await asyncio.gather(runner())


if __name__ == '__main__':
    asyncio.run(main())
