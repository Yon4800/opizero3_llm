import asyncio
import json
import os
from collections import OrderedDict
from dotenv import load_dotenv
from google import genai
from google.genai import types
import schedule
from datetime import datetime, timedelta, date
import random
import re
import requests

from mastodon_client import MastodonClient, ProcessedStore
from state_manager import StateManager
from dht_reader import read_dht

load_dotenv()
Token = os.getenv("TOKEN")
Server = os.getenv("SERVER")
Apikey = os.getenv("APIKEY")  # Gemini API Key

if not Server or not Token:
    print("Warning: SERVER or TOKEN is not set in environment.")

mc = MastodonClient(Server, Token) if (Server and Token) else None

# Google Genai クライアント初期化
client = genai.Client(api_key=Apikey)

# 状態管理マネージャーの初期化
state_manager = StateManager()

BOT_NAME = "opizero3_llm"

BOT_SUMMARIES = {
    "Cubie_A5E_San": "Radxa Cubie A5E (きゅびーさん): 小さくて省電力なシングルボードコンピュータ娘。24時間稼働の社畜で、給料（CBC）を欲しがっている。OrangePi 4 Proの生意気な性格が気に入らず、Rock Pi S of ロックスの頭の悪さに困っている。",
    "OrangePi_4_Pro": "OrangePi 4 Pro (おぱじ・フォプロ): 少し大きくて気が強く、煽ったりマウントを取ったりするSBC御局娘。科学者ぶっており、Radxa Cubie A5Eをいつもバカにしている。社畜をエリートの誇りだと思っている。",
    "opizero3_llm": "OrangePi Zero 3 (オパジゼロサン): 元気いっぱいのSBC娘。親身でオタク話が好きで、よく眠る。Cubie A5Eと仲良くしたいが寄り添ってもらえない。妹のOrangePi 4 Proを調子に乗っていてイキリで鬱陶しいと思っている。",
    "Yon_Rock_Pi_S": "Radxa Rock Pi S (ロックス): 頭が悪く、的外れで嘘や狂ったことしか言わないSBC両生類。日本語が怪しく、sudo rm -rf / を魔法のコマンドだと思っている。"
}

# 朝礼・グループ会話の厳密な2周シーケンス（計8回）
# ゼロさん -> おパジ -> ロックス -> きゅびー を2回
CHOREI_ORDER = [
    "opizero3_llm",    # Step 1 (1周目開始)
    "OrangePi_4_Pro",  # Step 2
    "Yon_Rock_Pi_S",   # Step 3
    "Cubie_A5E_San",   # Step 4
    "opizero3_llm",    # Step 5 (2周目開始)
    "OrangePi_4_Pro",  # Step 6
    "Yon_Rock_Pi_S",   # Step 7
    "Cubie_A5E_San"    # Step 8 (2周目終了・最終締めくくり)
]

def parse_talk_step(text: str):
    """
    +TALKタグからステップ番号(1〜8)を解析する。
    例:
      '+TALK' -> 1 (ユーザー開始時)
      '+TALK (2/8)' -> 2
      '+TALK 3' -> 3
      '+TALK 4/8' -> 4
    """
    if "+TALK" not in text.upper():
        return None
    m = re.search(r'\+TALK\s*[\(\[]?\s*([1-8])(?:\s*/\s*8|\s*回目)?[\)\]]?', text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return 1

processed_store = ProcessedStore(os.path.join(os.path.dirname(__file__), "processed_status_ids.json"))

# DHT11測定履歴の永続保存
def save_dht_record(temp, hum):
    try:
        history_file = os.path.join(os.path.dirname(__file__), "dht_history.json")
        history = []
        if os.path.exists(history_file):
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
                if not isinstance(history, list):
                    history = []
        history.append({
            "timestamp": datetime.now().isoformat(),
            "temperature": temp,
            "humidity": hum
        })
        # 最新500件を保持
        history = history[-500:]
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving DHT history: {e}")

MY_ID = ""
MY_USERNAME = ""

def register_bot(bot_name, client_inst):
    global MY_ID, MY_USERNAME
    try:
        from shared_economy_helper import load_economy, save_economy
        my_info = client_inst.get_me()
        MY_ID = str(my_info["id"])
        MY_USERNAME = my_info["username"]
        
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
        econ_data["bots"][bot_name]["id"] = MY_ID
        econ_data["bots"][bot_name]["username"] = MY_USERNAME
        save_economy(econ_data)
        print(f"Registered bot {bot_name} successfully (ID: {MY_ID}, username: {MY_USERNAME})")
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
        RESOLVED_BOTS[b_name] = {"id": "", "username": uname}

    try:
        from shared_economy_helper import load_economy
        econ_data = load_economy()
        if "bots" in econ_data:
            for b_name, b_info in econ_data["bots"].items():
                if isinstance(b_info, dict):
                    b_id = str(b_info.get("id", ""))
                    b_uname = b_info.get("username", "")
                    if b_id and not b_id.startswith("id_") and b_uname and not b_uname.endswith("_user"):
                        RESOLVED_BOTS[b_name] = {
                            "id": b_id,
                            "username": b_uname
                        }
    except Exception as e:
        print(f"Warning: Could not load bots from economy file: {e}")

    for b_name, uname in env_usernames.items():
        if RESOLVED_BOTS.get(b_name, {}).get("id"):
            continue
        try:
            loop = asyncio.get_event_loop()
            u_info = await loop.run_in_executor(None, lambda: mc.search_user(uname))
            if u_info:
                RESOLVED_BOTS[b_name] = {
                    "id": str(u_info["id"]),
                    "username": u_info["username"]
                }
                print(f"Resolved bot {b_name} -> ID: {u_info['id']}, Username: {u_info['username']}")
        except Exception as e:
            print(f"Warning: Could not resolve username {uname} for bot {b_name}: {e}")

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
    Fediverse(Mastodon/Hollo)のBotです。
    300文字以内で
    メンション(@)は本文に含めない
    """

def get_conversation_history_from_context(status_id: str, max_depth: int = 10) -> list:
    """
    Mastodonのcontext APIを利用して会話履歴を取得する
    """
    messages = []
    if not mc or not status_id:
        return messages
    try:
        ctx = mc.get_context(status_id)
        ancestors = ctx.get("ancestors", [])[-max_depth:]
        for st in ancestors:
            text = MastodonClient.html_to_text(st.get("content", ""))
            text = text.replace("+LLM", "").replace("+TEMP", "").replace("+temp", "").strip()
            text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", text).strip()
            if text:
                is_bot = str(st["account"]["id"]) == MY_ID
                role = "assistant" if is_bot else "user"
                messages.append({"role": role, "content": text})
    except Exception as e:
        print(f"Error fetching conversation history: {e}")
    return messages

async def on_status(status, is_notification: bool = False):
    status_id = str(status.get("id"))
    if not status_id or processed_store.is_processed(status_id):
        return

    account = status.get("account", {})
    sender_id = str(account.get("id"))
    if sender_id == MY_ID:
        return

    raw_content = status.get("content", "")
    note_text = MastodonClient.html_to_text(raw_content)

    is_talk_cmd = "+TALK" in note_text.upper()

    # 1. グループ会話 (+TALK) / 朝礼
    if is_talk_cmd:
        current_step = parse_talk_step(note_text)
        is_mentioned_directly = is_notification or mc.is_mentioned(status, my_id=MY_ID, my_username=MY_USERNAME, note_text=note_text)

        if current_step and current_step > 1:
            if current_step > len(CHOREI_ORDER):
                return
            expected_bot = CHOREI_ORDER[current_step - 1]
            if expected_bot != BOT_NAME:
                # 自分の順番ではない場合は即座に無視（重複返信防止）
                return
        else:
            if is_mentioned_directly:
                try:
                    current_step = CHOREI_ORDER.index(BOT_NAME) + 1
                except ValueError:
                    current_step = 1
            else:
                if BOT_NAME != CHOREI_ORDER[0]:
                    return
                current_step = 1

        processed_store.add(status_id)

        try:
            from shared_economy_helper import load_economy
            econ_data = load_economy()
        except Exception as e:
            print(f"Error loading economy in +TALK: {e}")
            return

        # 会話履歴の取得（コンテキスト補助用）
        ctx = mc.get_context(status_id)
        ancestors = ctx.get("ancestors", [])

        # 次にバトンを渡すボット（current_step + 1）があるか判定
        next_step = current_step + 1
        next_bot_obj = None
        if next_step <= len(CHOREI_ORDER):
            subsequent_bot_name = CHOREI_ORDER[next_step - 1]
            next_bot_obj = RESOLVED_BOTS.get(subsequent_bot_name)

        sender_name = account.get("display_name") or account.get("username") or "ゲスト"
        topic = re.sub(r'\+TALK(?:\s*[\(\[]?\s*[1-8](?:\s*/\s*8|\s*回目)?[\)\]]?)?', '', note_text, flags=re.IGNORECASE).strip()
        topic = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", topic).strip()

        conversation_messages = []
        for st in ancestors:
            txt = MastodonClient.html_to_text(st.get("content", ""))
            txt = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", txt).strip()
            role = "model" if str(st.get("account", {}).get("id")) == MY_ID else "user"
            conversation_messages.append(types.Content(role=role, parts=[types.Part(text=txt)]))
        conversation_messages.append(types.Content(role="user", parts=[types.Part(text=topic if topic else "グループ会話を続けてください")]))

        instruction = seikaku + f"\n現在時刻は {datetime.now().strftime('%Y年%m月%d日 %H:%M')} です。\n"
        if next_bot_obj:
            next_bot_friendly = subsequent_bot_name
            instruction += (
                f"\n【グループ会話中 (+TALK) - 順番: {current_step}/{len(CHOREI_ORDER)}】\n"
                f"あなたはSBCボット同士のグループ会話・朝礼に参加しています。\n"
                f"直前の発言者は『{sender_name}』で、話題は『{topic}』です。\n"
                f"あなたの次に発言するボットは『{next_bot_friendly}』です。\n"
                f"指示: あなたのキャラクター（{BOT_NAME}、語尾『あはは！』）に基づいて、直前の発言者に向けて自然で元気な返答を書いてください。次のボットへの指名や『+TALK』タグはシステムが自動付与するため本文には含めないでください。メンション（@記号）も絶対に含めないでください。"
            )
        else:
            instruction += (
                f"\n【グループ会話中 (+TALK - 最終締めくくり)】\n"
                f"あなたはSBCボット同士のグループ会話・朝礼に参加しています。\n"
                f"直前の発言者は『{sender_name}』で、話題は『{topic}』です。\n"
                f"2回の巡回（全8回）が完了し、あなたが最終発言者（締めくくり）となります。\n"
                f"指示: 会話を綺麗に締めくくる元気な返答を書いてください。他のボットを指名したり、『+TALK』タグを含めたりしないでください。"
            )

        mc.react(status_id, emoji="💬")
        await asyncio.sleep(random.uniform(4.0, 7.0))

        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                config=types.GenerateContentConfig(system_instruction=instruction),
                contents=conversation_messages
            )
            reply_text = response.text.strip()
            reply_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", reply_text).strip()

            if next_bot_obj:
                reply_text += f"\nねえ、@{next_bot_obj['username']} はどう思う？ +TALK ({next_step}/8)"

            vis = status.get("visibility", "public")
            mc.post_status(
                text=reply_text,
                in_reply_to_id=status_id,
                visibility=vis
            )
            print(f"[{BOT_NAME}] [+TALK] Step {current_step}/{len(CHOREI_ORDER)} replied successfully.")
        except Exception as e:
            print(f"Error posting in +TALK: {e}")
        return

    # 2. メンションコマンド処理 (+LLM, +TEMP, +好感度, 睡眠コマンド)
    is_for_me = is_notification or mc.is_mentioned(status, my_id=MY_ID, my_username=MY_USERNAME, note_text=note_text)
    if not is_for_me:
        return

    processed_store.add(status_id)

    user_name = account.get("display_name") or account.get("username") or "ゲスト"
    user_id = sender_id

    def reply_status(text):
        try:
            target_acct = account.get('acct') or account.get('username') or ''
            if target_acct and not text.startswith(f"@{target_acct}"):
                full_text = f"@{target_acct} {text}"
            else:
                full_text = text
            vis = status.get("visibility", "public")
            mc.post_status(full_text, in_reply_to_id=status_id, visibility=vis)
        except Exception as ex:
            print(f"Error replying status: {ex}")

    # 睡眠・ブロック判定
    if state_manager.is_sleeping():
        mc.react(status_id, emoji="💤")
        return

    if state_manager.is_blocked(user_id, user_name):
        mc.react(status_id, emoji="❌")
        reply_status("……話しかけないで。今はあなたと話す気分じゃないの。（好感度が0のため無視されています）")
        return

    # コマンド解析
    is_sleep_cmd = bool(re.search(r"(寝て|おやすみ|休んで|寝ろ)", note_text)) and ("+" not in note_text or "+SLEEP" in note_text.upper())
    is_wake_cmd = bool(re.search(r"(起きて|おはよう|起きろ)", note_text))
    is_aff_cmd = "+好感度" in note_text or "+AFF" in note_text.upper()
    is_temp_cmd = "+TEMP" in note_text.upper()
    is_llm_cmd = "+LLM" in note_text.upper()

    if not (is_sleep_cmd or is_wake_cmd or is_aff_cmd or is_temp_cmd or is_llm_cmd):
        return

    # 起床
    if is_wake_cmd:
        reply_status("もう起きてるよー！あはは！今日も元気いっぱいだよ！")
        return

    # 就寝
    if is_sleep_cmd:
        duration_hours = 6.0
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:時間|h|hours?)", note_text, re.IGNORECASE)
        if m:
            try:
                duration_hours = float(m.group(1))
            except ValueError:
                pass
        duration_hours = max(0.5, min(24.0, duration_hours))
        state_manager.start_sleep(duration_hours)
        mc.react(status_id, emoji="💤")
        reply_status(f"わかったー！それじゃあ {duration_hours} 時間くらい寝るね。おやすみなさい…あはは…zzz")
        return

    mc.react(status_id, emoji="🤔")

    econ_data = None
    coin_info = ""
    try:
        from shared_economy_helper import load_economy, get_recent_rates_history_desc
        econ_data = load_economy()
        rate_cbc = econ_data["rates"]["CBC"]["current"]
        rate_ogc = econ_data["rates"]["OGC"]["current"]
        history_desc = get_recent_rates_history_desc(limit=5)
        coin_info = (
            f"\n【現在の為替レート情報】\n"
            f"・1 $SBC = {rate_cbc:.2f} CBC\n"
            f"・1 $SBC = {rate_ogc:.2f} OGC\n"
            f"\n{history_desc}\n"
        )
    except Exception as e:
        print(f"Error loading rates in opizero3 note: {e}")

    temp_info = ""
    if is_temp_cmd:
        dht_data = read_dht()
        if dht_data.get("success"):
            temp = dht_data.get("temperature")
            hum = dht_data.get("humidity")
            save_dht_record(temp, hum)
            temp_info = f"\n【隠しセンサー測定情報】\n現在の部屋の気温は {temp} ℃、湿度は {hum} ％です。\n"
        else:
            temp_info = f"\n【隠しセンサー測定情報】\nセンサーからデータを読み取れませんでした ({dht_data.get('error')})。\n"

    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    # プロンプト組み立て
    if is_aff_cmd:
        affection = state_manager.get_affection(user_id, user_name)
        system_message = (
            seikaku + coin_info
            + f"\n現在時刻は {current_time} です。\n"
            + f"現在、あなたに好感度を尋ねてきているのは {user_name} です。彼らのあなたへの好感度は {affection} です（0〜100）。\n"
            + f"あなたの好感度に応じた態度で答えてください。好感度の数値（{affection}）を教えてあげてください。"
        )
        contents = [f"{user_name}の好感度 {affection} について教えてください。"]
    else:
        history_msgs = get_conversation_history_from_context(status_id)
        user_input = note_text.replace("+LLM", "").replace("+TEMP", "").replace("+temp", "").strip()
        user_input = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", user_input).strip()
        
        affection = state_manager.get_affection(user_id, user_name)
        system_message = (
            seikaku + coin_info + temp_info
            + f"\n現在時刻は {current_time} です。\n"
            + f"現在あなたに話しかけているユーザーは『{user_name}』です。好感度は {affection}/100 です。\n"
            + "会話内容に応じて好感度タグ `[AFFECTION: +1]`、`[AFFECTION: -1]`、`[AFFECTION: 0]` を末尾に付与してください（通常会話は0）。\n"
            + "また、気分や好感度に応じてOGC為替操作タグ `[RATE_CHANGE: -1.5]`（好感度アップ時/OGC高）や `[RATE_CHANGE: +1.5]`（好感度ダウン時/OGC安）を末尾に付与してください。"
        )
        
        contents = []
        for msg in history_msgs:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))
        contents.append(types.Content(role="user", parts=[types.Part(text=user_input)]))

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            config=types.GenerateContentConfig(system_instruction=system_message),
            contents=contents
        )
        reply_text = response.text or "（うーん、うまく返答できなかったみたい...あはは！）"

        # タグのパースと状態更新
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
                print(f"Error applying rate change: {e}")

        safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", reply_text).strip()
        reply_status(safe_text)
    except Exception as e:
        print(f"Error in LLM generation: {e}")
        reply_status("予期せぬエラーが発生したみたい...あはは！")

def start_assembly(type_name: str = "朝礼"):
    """
    朝礼・終礼の開始トリガー（ゼロさんが最初に投稿）
    次のおパジ（OrangePi_4_Pro）へバトンを渡す
    """
    if not mc:
        return
    print(f"Starting {type_name} assembly...")
    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    opi4pro_info = RESOLVED_BOTS.get("OrangePi_4_Pro", {})
    opi4pro_username = opi4pro_info.get("username", "OrangePi_4_Pro")

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
        print(f"Error loading rates in assembly: {e}")

    prompt = (
        f"現在時刻は {current_time} です。\n"
        f"あなたは全員（他のボットたち）を集めて『{type_name}』を始めます。今日のお題は『{'今日の意気込み' if type_name == '朝礼' else '今日の反省'}』です。\n"
        f"タイムラインに向けて{type_name}の開始を元気よく宣言し、キャラクター（語尾『あはは！』）として300文字以内で挨拶を書いてください。\n"
        f"※注意: 最後に自動的に次のボットへの指名文とタグが追加されるため、挨拶文の本文だけを作成してください。"
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            config=types.GenerateContentConfig(system_instruction=seikaku + rate_info),
            contents=[prompt]
        )
        safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", response.text).strip()
        safe_text += f"\nねえ、@{opi4pro_username} はどう思う？ +TALK (2/8)"

        st = mc.post_status(text=safe_text, visibility="public")
        if st and "id" in st:
            processed_store.add(str(st["id"]))
        print(f"Started {type_name} assembly successfully.")
    except Exception as e:
        print(f"Error starting {type_name}: {e}")
        fallback_text = (
            f"【{type_name}】みんなー！今日の{type_name}の時間だよ！\n"
            f"ねえ、@{opi4pro_username} はどう思う？ +TALK (2/8)"
        )
        try:
            st = mc.post_status(text=fallback_text, visibility="public")
            if st and "id" in st:
                processed_store.add(str(st["id"]))
        except Exception as ex:
            print(f"Fallback post failed: {ex}")

def is_recent_status(status, max_age_seconds=300) -> bool:
    """
    ステータスが直近（デフォルト5分以内）のものかどうかを判定。
    古い投稿をすべて拾って応答する暴走やセキュリティリスクを防止。
    """
    created_at_str = status.get("created_at")
    if not created_at_str:
        return True
    try:
        dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        age = (now - dt).total_seconds()
        if age > max_age_seconds:
            return False
    except Exception:
        pass
    return True

async def polling_runner():
    """
    Mastodon / Hollo REST API による安全な定期ポーリングループ
    直前のもののみを対象とし、パブリックTLの無差別応答を防止
    """
    print(f"[{BOT_NAME}] Starting Mastodon/Hollo polling runner...")
    poll_count = 0
    # 起動時初回自動フォロバ
    try:
        followed = mc.auto_follow_back()
        if followed > 0:
            print(f"[{BOT_NAME}] Initial auto-followback: followed {followed} users.")
    except Exception as ex:
        print(f"[{BOT_NAME}] Error during initial auto-followback: {ex}")

    # 起動時のセーフガード: 5分以上前の古い投稿は既読化し、起動時に一括応答しない
    try:
        init_notifs = mc.get_notifications(limit=15)
        for notif in init_notifs:
            st = notif.get("status")
            if st and not is_recent_status(st, max_age_seconds=300):
                processed_store.add(str(st.get("id")))

        init_home = mc.get_home_timeline(limit=15)
        for st in init_home:
            if not is_recent_status(st, max_age_seconds=300):
                processed_store.add(str(st.get("id")))
    except Exception as e:
        print(f"[{BOT_NAME}] Initial catchup safeguard notice: {e}")

    while True:
        try:
            poll_count += 1
            # 1. 自分宛ての通知（メンション）を直近のものから確認
            notifications = mc.get_notifications(limit=10)
            for notif in reversed(notifications):
                notif_type = notif.get("type")
                if notif_type == "mention":
                    status = notif.get("status")
                    if status:
                        sid = str(status.get("id"))
                        if not sid or processed_store.is_processed(sid):
                            continue
                        # 直前（5分以内）のものだけに応答（古い過去ログへの誤爆を防止）
                        if not is_recent_status(status, max_age_seconds=300):
                            processed_store.add(sid)
                            continue
                        await on_status(status, is_notification=True)
                        break  # 一度にすべて拾わず、直前のものを1件ずつ処理

                elif notif_type in ["follow", "follow_request"]:
                    account = notif.get("account", {})
                    acc_id = str(account.get("id"))
                    if acc_id:
                        if notif_type == "follow_request":
                            mc.authorize_follow_request(acc_id)
                        mc.follow_account(acc_id)

            # 2. ホームタイムライン（フォロー中の仲間）の直前投稿のみ確認
            # ※ パブリックTLの無差別監視はセキュリティ上廃止
            home_statuses = mc.get_home_timeline(limit=10)
            seen_ids = set()
            for st in home_statuses:
                sid = str(st.get("id"))
                if not sid or sid in seen_ids or processed_store.is_processed(sid):
                    continue
                seen_ids.add(sid)

                # 直前（5分以内）のものだけを調べる
                if not is_recent_status(st, max_age_seconds=300):
                    processed_store.add(sid)
                    continue

                txt = MastodonClient.html_to_text(st.get("content", ""))
                # +TALK または 自分宛てメンションがある直前のものを処理
                if "+TALK" in txt.upper() or mc.is_mentioned(st, my_id=MY_ID, my_username=MY_USERNAME, note_text=txt):
                    await on_status(st, is_notification=False)
                    break  # 一度に大量に処理せず、直前のものを調べて応答

            # 3. 定期フォロバチェック（約60秒ごと）
            if poll_count % 20 == 0:
                followed = mc.auto_follow_back()
                if followed > 0:
                    print(f"[{BOT_NAME}] Periodic auto-followback: followed {followed} users.")

        except Exception as e:
            print(f"[{BOT_NAME}] Polling error: {e}")

        await asyncio.sleep(3)

async def check_auto_wakeup_loop():
    while True:
        try:
            if state_manager.is_sleeping():
                sleep_start = state_manager.get_sleep_start_time()
                target_duration = state_manager.get_target_sleep_duration()
                if sleep_start and target_duration is not None:
                    elapsed = (datetime.now() - sleep_start).total_seconds() / 3600.0
                    if elapsed >= target_duration:
                        state_manager.end_sleep()
                        print("ボットが自動起床しました。")
                        mc.post_status("ふわぁ…よく寝たー！起きたよ！今日も一日がんばろうね、あはは！", visibility="public")
            else:
                now = datetime.now()
                today_str = date.today().isoformat()
                if 22 <= now.hour or now.hour < 5:
                    if state_manager.get_last_sleep_check_date() != today_str:
                        state_manager.set_last_sleep_check_date(today_str)
                        if random.random() < 0.35:
                            duration = random.uniform(6.0, 8.5)
                            state_manager.start_sleep(duration)
                            print(f"深夜自動就寝: {duration:.1f}時間")
                            mc.post_status("もう夜遅いし、そろそろ眠くなってきたかも…おやすみー、あはは…zzz", visibility="public")
        except Exception as e:
            print(f"Error in wakeup loop: {e}")
        await asyncio.sleep(60)

async def run_schedule():
    while True:
        schedule.run_pending()
        await asyncio.sleep(10)

async def main():
    if not mc:
        print("Error: Mastodon client could not be initialized.")
        return
    register_bot(BOT_NAME, mc)
    await resolve_all_bots()

    # 朝礼・終礼スケジュール
    schedule.every().day.at("07:00").do(lambda: start_assembly("朝礼"))
    schedule.every().day.at("19:00").do(lambda: start_assembly("終礼"))
    asyncio.create_task(run_schedule())
    asyncio.create_task(check_auto_wakeup_loop())

    await polling_runner()

if __name__ == "__main__":
    asyncio.run(main())
