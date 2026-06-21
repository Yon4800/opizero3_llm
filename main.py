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
from dht_reader import read_dht

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

BOT_NAME = "opizero3_llm"

BOT_SUMMARIES = {
    "Cubie_A5E_San": "Radxa Cubie A5E (きゅびーさん): 小さくて省電力なシングルボードコンピュータ娘。24時間稼働の社畜で、給料（CBC）を欲しがっている。OrangePi 4 Proの生意気な性格が気に入らず、Rock Pi S of ロックスの頭の悪さに困っている。",
    "OrangePi_4_Pro": "OrangePi 4 Pro (おぱじ・フォプロ): 少し大きくて気が強く、煽ったりマウントを取ったりするSBC御局娘。科学者ぶっており、Radxa Cubie A5Eをいつもバカにしている。社畜をエリートの誇りだと思っている。",
    "opizero3_llm": "OrangePi Zero 3 (オパジゼロサン): 元気いっぱいのSBC娘。親身でオタク話が好きで、よく眠る。Cubie A5Eと仲良くしたいが寄り添ってもらえない。妹のOrangePi 4 Proを調子に乗っていてイキリで鬱陶しいと思っている。",
    "Yon_Rock_Pi_S": "Radxa Rock Pi S (ロックス): 頭が悪く、的外れで嘘や狂ったことしか言わないSBC両生類。日本語が怪しく、sudo rm -rf / を魔法のコマンドだと思っている。"
}

def register_bot(bot_name, mk):
    try:
        from datetime import datetime, timedelta
        from shared_economy_helper import load_economy, save_economy
        my_info = mk.i()
        my_id = my_info["id"]
        my_username = my_info["username"]
        
        econ_data = load_economy()
        if "bots" not in econ_data:
            econ_data["bots"] = {}
            
        if bot_name not in econ_data["bots"]:
            econ_data["bots"][bot_name] = {
                "balance_cbc": 0.0,
                "last_salary_paid_time": (datetime.now() - timedelta(days=1)).isoformat(),
                "break_until": None,
                "virtual_pc_count": 0,
                "items": []
            }
        econ_data["bots"][bot_name]["id"] = my_id
        econ_data["bots"][bot_name]["username"] = my_username
        save_economy(econ_data)
        print(f"Registered bot {bot_name} successfully (ID: {my_id}, username: {my_username})")
    except Exception as e:
        print(f"Error registering bot: {e}")

RESOLVED_BOTS = {}

async def resolve_all_bots():
    global RESOLVED_BOTS
    env_usernames = {
        "Cubie_A5E_San": os.getenv("BOT_USER_CUBIE", "Cubie_A5E_San"),
        "OrangePi_4_Pro": os.getenv("BOT_USER_OPI4PRO", "OrangePi_4_Pro"),
        "opizero3_llm": os.getenv("BOT_USER_OPIZERO3", "opizero3_llm"),
        "Yon_Rock_Pi_S": os.getenv("BOT_USER_ROCKPIS", "Yon_Rock_Pi_S")
    }
    for b_name, uname in env_usernames.items():
        if not uname:
            continue
        try:
            loop = asyncio.get_event_loop()
            u_info = await loop.run_in_executor(None, lambda: mk.users_show(username=uname))
            if u_info:
                RESOLVED_BOTS[b_name] = {
                    "id": u_info["id"],
                    "username": u_info["username"]
                }
                print(f"Resolved bot {b_name} -> ID: {u_info['id']}, Username: {u_info['username']}")
        except Exception as e:
            print(f"Warning: Could not resolve username {uname} for bot {b_name}: {e}")

def get_talk_participants(note_id, mk):
    participants = set()
    current_note_id = note_id
    depth = 0
    while current_note_id and depth < 10:
        try:
            current_note = mk.notes_show(note_id=current_note_id)
            participants.add(current_note["userId"])
            current_note_id = current_note.get("replyId")
            depth += 1
        except Exception:
            break
    return participants

def get_talk_participant_counts(note_id, mk, bot_ids):
    counts = {bot_id: 0 for bot_id in bot_ids}
    current_note_id = note_id
    depth = 0
    while current_note_id and depth < 20:
        try:
            current_note = mk.notes_show(note_id=current_note_id)
            user_id = current_note["userId"]
            if user_id in counts:
                counts[user_id] += 1
            current_note_id = current_note.get("replyId")
            depth += 1
        except Exception:
            break
    return counts



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
    HDDが壊れることもあるので、そこが不満のようですが、最近USB補助電源を追加したため、落ちなくなりました。
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
    ロックスには、気温、湿度、気圧を測れる機能があり、キチガイゲージ機能もあり、ログインボーナス機能もあります。
    きゅびーさんには、CPUとRAMの使用率を測れる機能と、通貨変換機能や、FX機能があります
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
                elif data["body"]["type"] == "notification":
                    notification = data["body"]["body"]
                    if notification.get("type") in ["mention", "reply"]:
                        note = notification.get("note")
                        if note:
                            await on_note(note)
                    elif notification.get("type") == "followed":
                        user = notification.get("user")
                        if user:
                            await on_follow(user)
                elif data["body"]["type"] == "followed":
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
    # --- +TALK implementation ---
    note_text = note.get("text") or ""
    is_talk_cmd = "+TALK" in note_text.upper()

    if is_talk_cmd:
        if note["userId"] == MY_ID:
            return
            
        try:
            from shared_economy_helper import load_economy
            econ_data = load_economy()
        except Exception as e:
            print(f"Error loading economy in +TALK: {e}")
            return
            
        bots = RESOLVED_BOTS
        bot_ids = {bot["id"]: name for name, bot in bots.items() if "id" in bot}
        
        is_mentioned = (note.get("mentions") and MY_ID in note["mentions"])
        if not is_mentioned:
            return
            
        try:
            starting_note = note
            depth = 0
            while starting_note.get("replyId") and depth < 10:
                starting_note = mk.notes_show(note_id=starting_note["replyId"])
                depth += 1
            
            starting_mentions = [m for m in starting_note.get("mentions", []) if m in bot_ids]
        except Exception as e:
            print(f"Error resolving starting note in +TALK: {e}")
            starting_mentions = [MY_ID]
            
        if len(starting_mentions) <= 1:
            target_bot_ids = set(bot_ids.keys())
        else:
            target_bot_ids = set(starting_mentions)
            
        if note.get("replyId") is None:
            if starting_mentions and starting_mentions[0] != MY_ID:
                return
                
        history = get_conversation_history(note["id"])
        if len(history) >= 10:
            return
            
        counts = get_talk_participant_counts(note["id"], mk, bot_ids)
        
        # Determine max_rounds based on number of participants
        if len(target_bot_ids) == 4:
            max_rounds = 2
        else:
            max_rounds = 3
            
        # Group candidates to prevent immediate ping-pong
        sender_id = note["userId"]
        primary_candidates = []
        secondary_candidates = []
        
        for name, bot in bots.items():
            b_id = bot.get("id")
            if b_id and b_id != MY_ID and b_id in target_bot_ids:
                spoken_count = counts.get(b_id, 0)
                if spoken_count < max_rounds:
                    if b_id != sender_id:
                        primary_candidates.append(bot)
                    else:
                        secondary_candidates.append(bot)
                        
        next_bot = None
        if primary_candidates:
            next_bot = random.choice(primary_candidates)
        elif secondary_candidates:
            next_bot = random.choice(secondary_candidates)
            
        sender_id = note["userId"]
        sender_name = bot_ids.get(sender_id, note["user"].get("name") or note["user"].get("username") or "ゲスト")
        
        topic = note_text.replace("+TALK", "").replace("+talk", "").strip()
        topic = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", topic).strip()
        
        conversation_messages = []
        for msg in history:
            role = "model" if msg["role"] == "assistant" else "user"
            conversation_messages.append(
                types.Content(role=role, parts=[types.Part(text=msg["content"])])
            )
            
        from datetime import datetime
        instruction = seikaku + f"\n現在時刻は {datetime.now().strftime('%Y年%m月%d日 %H:%M')} です。\n"
        if next_bot:
            next_bot_friendly = "ボット"
            for name, b in bots.items():
                if b.get("id") == next_bot["id"]:
                    next_bot_friendly = name
                    break
            instruction += (
                f"\n【グループ会話中 (+TALK)】\n"
                f"あなたはSBCボット同士のグループ会話に参加しています。\n"
                f"会話履歴の最後の発言者は『{sender_name}』で、話しかけられたお題は『{topic}』です。\n"
                f"あなたの次に発言するボットは『{next_bot_friendly}』です。\n"
                f"指示: あなたのキャラクター設定（{BOT_NAME}）に基づいて、最後の発言者に向けて返答を書いてください。次のボットへの指名や『+TALK』タグは自動で付与されるため、本文には含めないでください。メンション（@記号）も絶対に含めないでください。"
            )
        else:
            instruction += (
                f"\n【グループ会話中 (+TALK - 最終回)】\n"
                f"あなたはSBCボット同士のグループ会話に参加しています。\n"
                f"会話履歴の最後の発言者は『{sender_name}』で、話しかけられたお題は『{topic}』です。\n"
                f"全ての指名ボットが発言し終えたため、あなたが最終発言者（締めくくり）となります。\n"
                f"指示: あなたのキャラクター設定（{BOT_NAME}）に基づいて、会話を綺麗に締めくくる返答を書いてください。他のボットを指名したり、『+TALK』タグを含めたり、メンションを含めたりしないでください。"
            )
            
        try:
            mk.notes_reactions_create(note_id=note["id"], reaction="💬")
        except Exception:
            pass
            
        await asyncio.sleep(random.uniform(5.0, 10.0))
        
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                config=types.GenerateContentConfig(system_instruction=instruction),
                contents=conversation_messages
            )
            reply_text = response.text.strip()
            reply_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", reply_text).strip()
            
            if next_bot:
                reply_text += f"\nねえ、@{next_bot['username']} はどう思う？ +TALK"
                mk.notes_create(
                    text=reply_text,
                    reply_id=note["id"],
                    visibility=NoteVisibility.HOME
                )
            else:
                mk.notes_create(
                    text=reply_text,
                    reply_id=note["id"],
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True
                )
        except Exception as e:
            print(f"Error generating/posting in opizero3_llm +TALK: {e}")
        return

    # --- Existing mention check ---
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
    is_temp_cmd = "+TEMP" in note_text.upper()

    # コマンドが何も含まれていない場合は無視
    if not (is_s_cmd or is_w_cmd or is_m_cmd or is_llm_cmd or is_temp_cmd):
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
    elif is_temp_cmd:
        reaction = "🌡️"
    
    try:
        mk.notes_reactions_create(note_id=note["id"], reaction=reaction)
    except Exception as e:
        print(f"リアクション作成エラー: {e}")

    # DHT11センサー情報の取得（おまけ・隠し機能）
    temp_info = ""
    is_temp_req = is_temp_cmd or any(w in note_text for w in ["温度", "湿度", "気温", "室温", "温湿度"])
    if is_temp_req:
        try:
            loop = asyncio.get_running_loop()
            temp, hum = await loop.run_in_executor(None, read_dht)
            if temp is not None:
                temp_info = f"\n[センサー情報]\n現在の室温は {temp:.1f}℃ です。湿度(参考)は {hum:.1f}% です。\n※注意: 元気いっぱいのSBC娘としてのキャラクター設定に関わらず、現在の室温の値（{temp:.1f}℃）と湿度の値（{hum:.1f}%）だけは正確にそのまま伝えてください。"
            else:
                temp_info = "\n[センサー情報]\nセンサーからの室温・湿度情報の取得に失敗しました。\n※注意: 元気いっぱいのSBC娘としてのキャラクター設定に関わらず、現在は『室温・湿度情報の測定に失敗した（測れなかった）』ということだけは絶対に正確にそのまま伝えてください（架空の室温の数値をでっち上げたりしないでください）。"
        except Exception as e:
            print(f"DHT11読み取りエラー: {e}")

    try:
        coin_info = ""
        try:
            from shared_economy_helper import load_economy, save_economy, get_user_state, get_recent_rates_history_desc
            econ_data = load_economy()
            user_name_real = note["user"].get("name") or note["user"].get("username") or "ゲスト"
            username_real = note["user"].get("username", "")
            user_state = get_user_state(econ_data, note["userId"], username_real, user_name_real)
            user_state["balance_ogc"] = round(user_state["balance_ogc"] + 100.0, 2)
            save_economy(econ_data)
            
            rate_cbc = econ_data["rates"]["CBC"]["current"]
            rate_ogc = econ_data["rates"]["OGC"]["current"]
            user_cbc = user_state["balance_cbc"]
            user_ogc = user_state["balance_ogc"]
            user_sbc = user_state["balance_sbc"]
            history_desc = get_recent_rates_history_desc(limit=5)
            coin_info = (
                f"\n【通貨および資産情報】\n"
                f"・現在の為替レート:\n"
                f"  1 $SBC = {rate_cbc:.2f} CBC\n"
                f"  1 $SBC = {rate_ogc:.2f} OGC\n"
                f"\n{history_desc}\n"
                f"・話しかけているユーザー（{user_name}）の資産残高:\n"
                f"  CBC残高: {user_cbc:.2f} CBC\n"
                f"  OGC残高: {user_ogc:.2f} OGC\n"
                f"  $SBC残高: {user_sbc:.2f} $SBC\n"
            )
        except Exception as ex:
            print(f"Error updating economy in OrangePi Zero 3: {ex}")

        def reply_note(text):
            final_text = text
            mk.notes_create(
                text=final_text,
                reply_id=note["id"],
                visibility=NoteVisibility.HOME,
                no_extract_mentions=True,
            )

        current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        
        if is_s_cmd:
            # 寝る時間の判定（夜21:00〜朝06:00以外は「変な時間」として拒否する）
            now = datetime.now()
            if not (21 <= now.hour or now.hour < 6):
                # 変な時間に寝かせようとしたため拒否して怒る
                new_affection = state_manager.change_affection(user_id, -3, user_name)
                system_message = (
                    seikaku
                    + coin_info
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
                    + coin_info
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
                    + coin_info
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
                        + coin_info
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
                        + coin_info
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
                + coin_info
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
            
        elif is_llm_cmd or is_temp_cmd:
            # 通常会話 (+LLM / +TEMP)
            # 会話履歴を取得
            conversation_messages = get_conversation_history(note["id"])
            
            # 現在のメッセージを追加
            user_input = note_text.replace("+LLM", "").replace("+TEMP", "").replace("+temp", "").strip()
            user_input = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", user_input).strip()
            
            conversation_messages.append({
                "role": "user",
                "content": user_input
            })
            
            affection = state_manager.get_affection(user_id, user_name)
            system_message = (
                seikaku
                + coin_info
                + f"\n現在時刻は {current_time} です。\n"
                + f"現在、あなたに話しかけているのは {user_name} です。彼らの現在のあなたへの好感度は {affection} です（0〜100）。この好感度に応じた態度（80-100:非常に好意的、40-79:普通にフレンドリー、1-39:やや冷たい、0:極めて冷淡・無視）で会話に答えてください。好感度の具体的な数値（例：50など）は返答メッセージに含めないでください。\n"
                + "また、今回の会話の内容や相手の態度に応じて、好感度を変動させてください。返答の最後に必ず `[AFFECTION: +1]`、`[AFFECTION: -1]`、または `[AFFECTION: 0]` のいずれかのタグを付与してください（ユーザーに表示する返答メッセージには含めないでください）。\n"
                + "【重要】普通の会話や質問、挨拶など、日常的なやり取りでは好感度を上げ下げせず「0」にしてください。相手が非常に親切にしてくれたり、あなたを大いに喜ばせたり、素晴らしい贈り物の言葉をくれた場合のみ「+1」にし、逆に極めて失礼、侮辱的、または不快な発言をした場合のみ「-1」にしてください。普段は基本的に「0」を維持すること。"
            )
            rate_rules = (
                "\n【会話による為替操作ルール】\n"
                "・あなたは会話相手とのやり取りや好感度の変動に応じて、あなたの国の通貨（OGC）の為替レートを動かすことができます。\n"
                "・好感度が上がった場合（喜んだ場合）は、レートを下げて（通貨高／OGC高にして）ください。\n"
                "  タグの形式: `[RATE_CHANGE: -X.X]`（例: `[RATE_CHANGE: -1.5]`）を指定してください。変動幅は -0.5 から -5.0 の間で選んでください。\n"
                "・好感度が下がった場合（怒ったり悲しんだりした場合）は、レートを上げて（通貨安／OGC安にして）ください。\n"
                "  タグの形式: `[RATE_CHANGE: +X.X]`（例: `[RATE_CHANGE: +1.5]`）を指定してください。変動幅は +0.5 から +5.0 の間で選んでください。\n"
                "・特に変化がない場合は、タグを出力しないでください。\n"
                "・タグは好感度タグ `[AFFECTION: +1]` などと一緒に、メッセージの最後に付与してください。"
            )
            system_message += rate_rules
            
            history = []
            for msg in conversation_messages[:-1]:
                role = "model" if msg["role"] == "assistant" else "user"
                history.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))
            
            last_user_message = conversation_messages[-1]["content"]
            contents = history + [types.Content(role="user", parts=[types.Part(text=last_user_message)])]

        if temp_info:
            system_message += temp_info

        # LLMリクエスト送信
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            config=types.GenerateContentConfig(
                system_instruction=system_message
            ),
            contents=contents
        )
        
        reply_text = response.text
        
        # 好感度タグと為替操作タグのパース
        delta = 0
        if is_llm_cmd or is_temp_cmd:
            match_aff = re.search(r"\[AFFECTION:\s*([+-]?\d+)\]", reply_text)
            if match_aff:
                delta = int(match_aff.group(1))
                reply_text = re.sub(r"\[AFFECTION:\s*[+-]?\d+\]", "", reply_text).strip()
            
            if delta != 0:
                state_manager.change_affection(user_id, delta, user_name)
                
            match_rate = re.search(r"\[RATE_CHANGE:\s*([+-]?\d+(?:\.\d+)?)\]", reply_text)
            if match_rate:
                try:
                    from shared_economy_helper import apply_rate_change, save_economy
                    rate_delta = float(match_rate.group(1))
                    apply_rate_change(econ_data, "OGC", rate_delta)
                    save_economy(econ_data)
                    reply_text = re.sub(r"\[RATE_CHANGE:\s*[+-]?\d+(?:\.\d+)?\]", "", reply_text).strip()
                except Exception as e:
                    print(f"Error applying rate change in opizero3 general talk: {e}")
                
        safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", reply_text).strip()

        reply_note(safe_text)
        
    except Exception as e:
        reply_note("予期せぬエラーが発生したみたい...")
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
                        
                        rate_info = ""
                        try:
                            from shared_economy_helper import load_economy, get_recent_rates_history_desc
                            econ_data = load_economy()
                            rate_cbc = econ_data["rates"]["CBC"]["current"]
                            rate_ogc = econ_data["rates"]["OGC"]["current"]
                            history_desc = get_recent_rates_history_desc(limit=5)
                            rate_info = (
                                f"\n【現在の為替レート情報】\n"
                                f"・1 $SBC = {rate_cbc:.2f} CBC\n"
                                f"・1 $SBC = {rate_ogc:.2f} OGC\n"
                                f"\n{history_desc}\n"
                            )
                        except Exception as e:
                            print(f"Error loading rates in check_auto_wakeup_loop: {e}")

                        current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
                        system_message = (
                            seikaku 
                            + rate_info
                            + f"\n現在時刻は {current_time} です。あなたは十分に寝て（睡眠時間：{elapsed:.1f}時間）、自然に目が覚めました。タイムラインにみんなに向けた朝 of 朝の挨拶をキャラクターとして300文字以内で投稿してください。語尾は「あはは！」です。"
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
                            
                            rate_info = ""
                            try:
                                from shared_economy_helper import load_economy, get_recent_rates_history_desc
                                econ_data = load_economy()
                                rate_cbc = econ_data["rates"]["CBC"]["current"]
                                rate_ogc = econ_data["rates"]["OGC"]["current"]
                                history_desc = get_recent_rates_history_desc(limit=5)
                                rate_info = (
                                    f"\n【現在の為替レート情報】\n"
                                    f"・1 $SBC = {rate_cbc:.2f} CBC\n"
                                    f"・1 $SBC = {rate_ogc:.2f} OGC\n"
                                    f"\n{history_desc}\n"
                                )
                            except Exception as e:
                                print(f"Error loading rates in check_auto_wakeup_loop: {e}")

                            current_time = now.strftime("%Y年%m月%d日 %H:%M")
                            system_message = (
                                seikaku 
                                + rate_info
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
    register_bot(BOT_NAME, mk)
    await resolve_all_bots()
    # 自動起床ループをタスクとして起動
    asyncio.create_task(check_auto_wakeup_loop())
    await asyncio.gather(runner())


if __name__ == '__main__':
    asyncio.run(main())
