import streamlit as st
import os
import datetime
import requests
import json
import re
import html
import urllib.parse
import uuid
import pandas as pd
from openai import OpenAI
from dotenv import load_dotenv
import googlemaps
from supabase import create_client, Client
from location_utils import get_current_location, reverse_geocode

# 1. 初期設定
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
gmaps = googlemaps.Client(key=os.getenv('GOOGLE_MAPS_API_KEY'))
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# --- セッション状態の初期化 ---
if "final_plan" not in st.session_state:
    st.session_state.final_plan = None
if "last_plan_a" not in st.session_state:
    st.session_state.last_plan_a = None
if "last_plan_b" not in st.session_state:
    st.session_state.last_plan_b = None
if "context_info" not in st.session_state:
    st.session_state.context_info = ""
if "share_url" not in st.session_state:
    st.session_state.share_url = None
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "sb_access_token" not in st.session_state:
    st.session_state.sb_access_token = None
if "sb_refresh_token" not in st.session_state:
    st.session_state.sb_refresh_token = None
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "session_logged" not in st.session_state:
    st.session_state.session_logged = False
for k in ["current_lat", "current_lng", "current_location_name", "last_realtime_lat", "last_realtime_lng"]:
    if k not in st.session_state:
        st.session_state[k] = None
# 表示設定（ウィジェットにはvalueを渡さず、ここで初期値を入れる）
if "font_scale" not in st.session_state:
    st.session_state.font_scale = 100
if "button_scale" not in st.session_state:
    st.session_state.button_scale = 100
if "outdoor_mode" not in st.session_state:
    st.session_state.outdoor_mode = False

# --- 予算（総予算 + カテゴリ別） ---
BUDGET_LEVELS = ["節約", "標準", "贅沢"]
# 総予算スライダーは段階表示のため、1人あたり・旅行全体の目安額（円）に換算して扱う
BUDGET_LEVEL_YEN = {"節約": 30000, "標準": 60000, "贅沢": 120000}
BUDGET_CATEGORIES = [
    ("food", "budget_food", "🍽️ 食費", 0.30),
    ("transport", "budget_transport", "🚆 交通費", 0.20),
    ("sightseeing", "budget_sightseeing", "🎫 観光・入場料", 0.15),
    ("lodging", "budget_lodging", "🏨 宿泊", 0.25),
    ("other", "budget_other", "🛍️ その他", 0.10),
]
if "budget_total" not in st.session_state:
    st.session_state.budget_total = BUDGET_LEVELS[0]
if "budget_breakdown_on" not in st.session_state:
    st.session_state.budget_breakdown_on = False
if "budget_breakdown_initialized" not in st.session_state:
    st.session_state.budget_breakdown_initialized = False
for _, state_key, _, _ in BUDGET_CATEGORIES:
    if state_key not in st.session_state:
        st.session_state[state_key] = 0
    else:
        # トグルOFFで入力欄が非表示の間もウィジェットの値が消えないように保持する
        st.session_state[state_key] = st.session_state[state_key]
if "plan_budget" not in st.session_state:
    st.session_state.plan_budget = None

# --- 認証セッションの復元（Streamlitは再実行のたびにsupabaseクライアントを作り直すため） ---
if st.session_state.sb_access_token and st.session_state.sb_refresh_token:
    try:
        supabase.auth.set_session(st.session_state.sb_access_token, st.session_state.sb_refresh_token)
    except Exception:
        st.session_state.user_id = None
        st.session_state.user_email = None
        st.session_state.sb_access_token = None
        st.session_state.sb_refresh_token = None

# --- 2. 関数定義 ---
def _weather_from_response(data):
    if data["cod"] == 200:
        weather = data["weather"][0]["description"]
        temp = data["main"]["temp"]
        return {"desc": weather, "temp": temp, "text": f"天気：{weather} / 気温：{temp}°C"}
    return None

def get_weather_info(city_name):
    """世界中の都市に対応した天気情報を取得する"""
    api_key = os.getenv("OPENWEATHER_API_KEY")
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city_name}&appid={api_key}&units=metric&lang=ja"
    try:
        response = requests.get(url, timeout=5)
        return _weather_from_response(response.json())
    except: pass
    return None

def get_weather_by_coords(lat, lng):
    """緯度経度から現在地の天気情報を取得する"""
    api_key = os.getenv("OPENWEATHER_API_KEY")
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lng}&appid={api_key}&units=metric&lang=ja"
    try:
        response = requests.get(url, timeout=5)
        return _weather_from_response(response.json())
    except: pass
    return None

def get_clothing_tip(temp, weather_desc):
    """気温に基づいた服装アドバイス"""
    tip = ""
    if temp >= 25: tip += "👕 半袖で快適に過ごせます。"
    elif temp >= 20: tip += "👕 薄手の長袖や、羽織りものがあると安心です。"
    elif temp >= 15: tip += "🧥 ジャケットやトレンチコートがちょうど良い季節です。"
    elif temp >= 7: tip += "🧥 厚手のコートやニットで防寒しましょう。"
    else: tip += "🧣 厚手のコートにマフラーや手袋などの防寒具が必須です。"
    if any(s in weather_desc for s in ["雨", "雪", "雷"]):
        tip += "\n☔ 雨予報が含まれます。傘を忘れずに持っていきましょう。"
    return tip

def search_transit_status(area):
    url = "https://google.serper.dev/search"
    query = f"{area} 鉄道 運行情報 遅延 混雑 最新"
    payload = json.dumps({"q": query, "gl": "jp", "hl": "ja"})
    headers = {'X-API-KEY': os.getenv("SERPER_API_KEY"), 'Content-Type': 'application/json'}
    try:
        response = requests.post(url, headers=headers, data=payload)
        results = response.json().get("organic", [])
        if results: return f"- 🚆 [現在の運行・混雑情報（最新）]({results[0].get('link')})"
    except: pass
    return "- 🚆 運行情報の取得に失敗しました。"

def generate_google_maps_route(origin, dest):
    origin_enc = urllib.parse.quote(origin)
    dest_enc = urllib.parse.quote(dest)
    route_url = f"https://www.google.com/maps/dir/?api=1&origin={origin_enc}&destination={dest_enc}&travelmode=transit"
    return f"- 🗺️ [{origin}から{dest}へのルート案内]({route_url})"

def search_diverse_links(destination):
    queries = {"official": f"{destination} 観光 公式サイト", "blog": f"{destination} 観光 おすすめ ブログ", "review": f"{destination} 観光スポット 口コミ"}
    headers = {'X-API-KEY': os.getenv("SERPER_API_KEY"), 'Content-Type': 'application/json'}
    diverse_links = []
    settings = {"official": 1, "blog": 2, "review": 2}
    for key, query in queries.items():
        payload = json.dumps({"q": query, "gl": "jp", "hl": "ja"})
        try:
            response = requests.post("https://google.serper.dev/search", headers=headers, data=payload)
            results = response.json().get("organic", [])
            for item in results[:settings[key]]:
                label = "🏛️公式サイト" if key == "official" else "📝体験ブログ" if key == "blog" else "⭐レビュー"
                diverse_links.append(f"- {label}: [{item.get('title')}]({item.get('link')})")
        except: continue
    return "\n".join(diverse_links)

def search_web_assets(query, search_type="search"):
    url = f"https://google.serper.dev/{search_type}"
    payload = json.dumps({"q": query, "gl": "jp", "hl": "ja"})
    headers = {'X-API-KEY': os.getenv("SERPER_API_KEY"), 'Content-Type': 'application/json'}
    try:
        response = requests.post(url, headers=headers, data=payload)
        results = response.json()
        if search_type == "images":
            assets = ""
            for item in results.get("images", [])[:2]:
                assets += f"![image]({item.get('imageUrl')})\n"
            return assets
    except: return ""

def save_plan_and_get_url(plan_content, plan_a, plan_b, budget=None):
    result = supabase.table("plans").insert({
        "plan_content": plan_content,
        "plan_a": plan_a,
        "plan_b": plan_b,
        "budget": budget
    }).execute()
    plan_id = result.data[0]["id"]
    return f"https://jdgmmdxjnzzyxbpwnk3g7c.streamlit.app/?plan_id={plan_id}"

def load_plan_by_id(plan_id):
    result = supabase.table("plans").select("plan_content, plan_a, plan_b, budget").eq("id", plan_id).single().execute()
    return result.data if result.data else None

def save_plan_record(destination, travel_date, plan_content, plan_a, plan_b, user_id, is_public, budget=None):
    supabase.table("plans").insert({
        "destination": destination,
        "travel_date": travel_date.isoformat(),
        "plan_content": plan_content,
        "plan_a": plan_a,
        "plan_b": plan_b,
        "user_id": user_id,
        "is_public": is_public,
        "budget": budget
    }).execute()

def get_saved_plans_for_user(user_id):
    result = supabase.table("plans") \
        .select("id, destination, travel_date, created_at, plan_content, plan_a, plan_b, is_public, budget") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .execute()
    return result.data if result.data else []

def delete_plan(plan_id):
    supabase.table("plans").delete().eq("id", plan_id).execute()

def get_public_plans():
    result = supabase.table("plans") \
        .select("id, destination, travel_date, created_at, plan_content, plan_a, plan_b, budget") \
        .eq("is_public", True) \
        .order("created_at", desc=True) \
        .execute()
    return result.data if result.data else []

def log_event(event_type, metadata=None):
    try:
        supabase.table("usage_logs").insert({
            "event_type": event_type,
            "session_id": st.session_state.session_id,
            "metadata": metadata
        }).execute()
    except Exception:
        pass

def get_event_counts():
    result = supabase.table("usage_logs").select("event_type").execute()
    counts = {}
    for row in result.data:
        event_type = row.get("event_type")
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts

def _distinct_session_count(event_types):
    result = supabase.table("usage_logs").select("session_id").in_("event_type", event_types).execute()
    session_ids = {row["session_id"] for row in result.data if row.get("session_id")}
    return len(session_ids)

def get_funnel_counts():
    return {
        "visited": _distinct_session_count(["session_started"]),
        "generated": _distinct_session_count(["plan_generated"]),
        "action_taken": _distinct_session_count(["share_url_created", "plan_saved", "plan_copied"])
    }

def get_place_details_text(place_name):
    try:
        result = gmaps.places(query=place_name)
        if result['status'] == 'OK':
            place_id = result['results'][0]['place_id']
            details = gmaps.place(place_id=place_id, language='ja')
            name = details['result']['name']
            status = "営業中" if details['result'].get('opening_hours', {}).get('open_now') else "閉店中"
            weekday_text = details['result'].get('opening_hours', {}).get('weekday_text', '情報なし')
            hours = "\n".join(weekday_text) if isinstance(weekday_text, list) else weekday_text
            return f"【Google Maps情報：{name}】\n状況：{status}\n営業時間：\n{hours}"
    except: return "営業時間取得失敗"

# --- 3. エージェント設定 ---
ROLES = {
    "A": "あなたは【旅の理想・ワクワク担当】です。日本語で、1〜3時間のゆとりあるブロック形式のプランを提案してください。画像やリンクも活用して。",
    "B": (
        "あなたは【現実の制約・ブレーキ担当】です。必ず日本語で回答してください。移動距離や天候リスク、営業時間を厳密にチェックし、無理がないか批判的に検討してください。"
        "前提条件に『カテゴリ別予算』がある場合は、食費・交通費・観光・入場料・宿泊・その他のカテゴリごとに概算費用を見積もって予算超過の可能性をチェックし、"
        "超過しそうなカテゴリには、より安い移動手段・食事場所・施設などの具体的な代替案を出してください。"
    ),
    "C": (
        "あなたは【まとめ担当】です。日本語で最終案を出してください。冒頭に『☀️当日のコンディション』、最後に提供されたリンクを全て含む『🔗旅の参考リンク集』を必ず掲載してください。"
        "プラン本文中の時刻（例：**14:30**）と金額（例：**1,500円**）は必ず **太字** にしてください。\n"
        "さらに、『🔗旅の参考リンク集』の後、回答の一番最後に、旅行中に急いで見ても分かる重要情報を次の形式で必ず出力してください。"
        "ブロックはコードブロックで囲まず、JSONは1行で出力し、ブロックの後には何も書かないでください。\n"
        "<<<KEY_INFO>>>\n"
        '{"items":[{"level":"danger|warning|info","icon":"絵文字1つ","title":"20字以内","detail":"40字以内"}],'
        '"budget_estimate":{"food":12000,"transport":8000,"sightseeing":3000,"lodging":0,"other":2000}}\n'
        "<<<END_KEY_INFO>>>\n"
        "level の基準：danger＝時間の締切（フライト、終電、閉館・最終入場、予約時刻）、"
        "warning＝注意（雨、定休日、混雑、長距離移動）、info＝次の行動。"
        "items は重要度の高い順に最大5件としてください。"
        "budget_estimate には、このプランにかかる1人あたりの概算費用を、食費(food)・交通費(transport)・観光・入場料(sightseeing)・宿泊(lodging)・その他(other)"
        "の5カテゴリすべてについて、円の整数（カンマや単位なし）で必ず出力してください。海外旅行の場合も円に換算した概算にしてください。"
        "カテゴリ別予算が指定されていない場合も budget_estimate は必ず出力してください。"
    )
}

def ask_agent(role_prompt, context, user_input):
    system_content = f"{role_prompt}\n\n【前提条件】\n{context}"
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": system_content}, {"role": "user", "content": user_input}]
    )
    return response.choices[0].message.content

# --- 表示サイズ調整 ---
OUTDOOR_FONT_SCALE = 140
OUTDOOR_BUTTON_SCALE = 160

def apply_display_settings():
    """session_stateの表示設定からCSSを生成して注入する"""
    outdoor = st.session_state.get("outdoor_mode", False)
    if outdoor:
        font = OUTDOOR_FONT_SCALE / 100
        btn = OUTDOOR_BUTTON_SCALE / 100
    else:
        font = st.session_state.get("font_scale", 100) / 100
        btn = st.session_state.get("button_scale", 100) / 100

    # 対象はメイン画面とサイドバー（Streamlitのヘッダー/ツールバーには効かせない）
    areas = ['[data-testid="stMain"]', '[data-testid="stSidebar"]']

    def sel(*parts):
        return ",\n".join(f"{a} {p}" for a in areas for p in parts)

    buttons = sel(
        '[data-testid="stButton"] button',
        '[data-testid="stDownloadButton"] button',
        '[data-testid="stFormSubmitButton"] button',
        '[data-testid="stLinkButton"] a',
        '[data-testid="stPopover"] button',
    )
    button_labels = sel(
        '[data-testid="stButton"] button [data-testid="stMarkdownContainer"] p',
        '[data-testid="stDownloadButton"] button [data-testid="stMarkdownContainer"] p',
        '[data-testid="stFormSubmitButton"] button [data-testid="stMarkdownContainer"] p',
        '[data-testid="stLinkButton"] a [data-testid="stMarkdownContainer"] p',
        '[data-testid="stPopover"] button [data-testid="stMarkdownContainer"] p',
    )

    css = f"""
<style>
:root {{ --font-scale: {font}; }}
{sel('[data-testid="stMarkdownContainer"]',
     '[data-testid="stMarkdownContainer"] p',
     '[data-testid="stMarkdownContainer"] li',
     '[data-testid="stWidgetLabel"] p')} {{
    font-size: calc(1rem * {font}) !important;
}}
{sel('[data-testid="stCaptionContainer"]', '[data-testid="stCaptionContainer"] p')} {{
    font-size: calc(0.875rem * {font}) !important;
}}
{sel('h1')} {{ font-size: calc(2.75rem * {font}) !important; }}
{sel('h2')} {{ font-size: calc(2.25rem * {font}) !important; }}
{sel('h3')} {{ font-size: calc(1.75rem * {font}) !important; }}
{sel('h4')} {{ font-size: calc(1.5rem * {font}) !important; }}
{sel('input', 'textarea', '[data-testid="stSelectbox"] div')},
[data-testid="stChatInputTextArea"] {{
    font-size: calc(1rem * {font}) !important;
}}
{buttons} {{
    min-height: max(48px, calc(2.5rem * {btn})) !important;
    padding: calc(0.25rem * {btn}) calc(0.75rem * {btn}) !important;
    font-size: calc(1rem * {btn}) !important;
}}
{button_labels} {{
    font-size: calc(1rem * {btn}) !important;
}}
</style>
"""

    if outdoor:
        css += f"""
<style>
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stSidebar"],
[data-testid="stSidebar"] > div {{
    background-color: #ffffff !important;
}}
[data-testid="stSidebar"] {{
    border-right: 2px solid #000000 !important;
}}
{sel('[data-testid="stMarkdownContainer"]', '[data-testid="stMarkdownContainer"] *',
     '[data-testid="stCaptionContainer"]', '[data-testid="stCaptionContainer"] *',
     '[data-testid="stWidgetLabel"] *', 'h1', 'h2', 'h3', 'h4',
     'input', 'textarea')} {{
    color: #000000 !important;
}}
{sel('a', '[data-testid="stMarkdownContainer"] a')} {{
    color: #0000cc !important;
    font-weight: 700 !important;
    text-decoration: underline !important;
}}
{sel('input', 'textarea')},
[data-testid="stChatInputTextArea"] {{
    background-color: #ffffff !important;
    color: #000000 !important;
}}
{buttons} {{
    border: 2px solid #000000 !important;
}}
</style>
"""
    st.markdown(css, unsafe_allow_html=True)

def render_display_settings():
    """タイトル直下に表示設定UIを置く（popoverが無い古いStreamlitではexpanderで代用）"""
    container = st.popover("⚙️ 表示設定") if hasattr(st, "popover") else st.expander("⚙️ 表示設定")
    with container:
        outdoor = st.session_state.outdoor_mode
        st.slider("文字サイズ（%）", min_value=100, max_value=160, step=10, key="font_scale", disabled=outdoor)
        st.slider("ボタンサイズ（%）", min_value=100, max_value=200, step=10, key="button_scale", disabled=outdoor)
        st.toggle("☀️ 屋外モード", key="outdoor_mode")
        if outdoor:
            st.caption(f"屋外モード中：文字{OUTDOOR_FONT_SCALE}%・ボタン{OUTDOOR_BUTTON_SCALE}%・高コントラストで表示しています")

# --- 重要情報カード ---
# ```json で囲まれて返ってきた場合もフェンスごと取り除く
KEY_INFO_PATTERN = re.compile(
    r"(?:```[a-zA-Z]*\s*)?<<<KEY_INFO>>>(.*?)<<<END_KEY_INFO>>>(?:\s*```)?", re.DOTALL
)
# 終了マーカーがない（出力が途中で切れた）場合は開始マーカー以降を捨てる
KEY_INFO_UNCLOSED_PATTERN = re.compile(r"(?:```[a-zA-Z]*\s*)?<<<KEY_INFO>>>.*\Z", re.DOTALL)
KEY_INFO_LEVELS = {
    "danger": {"label": "⛔ 締切", "color": "#c62828", "bg": "#fdecea"},
    "warning": {"label": "⚠️ 注意", "color": "#f9a825", "bg": "#fff8e1"},
    "info": {"label": "➡️ 次の行動", "color": "#1565c0", "bg": "#e3f2fd"},
}

def _parse_yen(value):
    """12000 / "12,000" / "¥12,000" / "12000円" を 0以上の整数に。解釈できなければ None"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value)) if value >= 0 else None
    if isinstance(value, str):
        cleaned = re.sub(r"[,¥￥円\s]", "", value)
        try:
            number = float(cleaned)
        except ValueError:
            return None
        return int(round(number)) if number >= 0 else None
    return None

def _parse_budget_estimate(raw_estimate):
    """budget_estimate を {カテゴリ: 円} に正規化する。形が壊れていれば None"""
    if not isinstance(raw_estimate, dict):
        return None
    estimate = {}
    for category, _, _, _ in BUDGET_CATEGORIES:
        if category not in raw_estimate:
            estimate[category] = 0
            continue
        yen = _parse_yen(raw_estimate[category])
        if yen is None:
            return None
        estimate[category] = yen
    if not any(c in raw_estimate for c, _, _, _ in BUDGET_CATEGORIES):
        return None
    return estimate

def parse_key_info(text):
    """プラン全文から重要情報ブロックを取り出し、(ブロックを除いた本文, items, budget_estimate) を返す"""
    if not text:
        return text or "", [], None
    items = []
    budget_estimate = None
    match = KEY_INFO_PATTERN.search(text)
    if match:
        raw = match.group(1).strip()
        raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw)
        try:
            data = json.loads(raw)
            raw_items = data.get("items", []) if isinstance(data, dict) else []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                detail = str(item.get("detail") or "").strip()
                if not title and not detail:
                    continue
                level = str(item.get("level") or "").strip().lower()
                items.append({
                    "level": level if level in KEY_INFO_LEVELS else "info",
                    "icon": str(item.get("icon") or "").strip(),
                    "title": title,
                    "detail": detail,
                })
                if len(items) >= 5:
                    break
            if isinstance(data, dict):
                budget_estimate = _parse_budget_estimate(data.get("budget_estimate"))
        except (ValueError, TypeError, AttributeError):
            items = []
            budget_estimate = None
    body = KEY_INFO_PATTERN.sub("", text)
    body = KEY_INFO_UNCLOSED_PATTERN.sub("", body)
    return body.rstrip(), items, budget_estimate

def render_key_info(items):
    """重要情報を danger→warning→info の順にカード表示する（itemsが空なら何も出さない）"""
    if not items:
        return
    order = list(KEY_INFO_LEVELS)
    sorted_items = sorted(items, key=lambda i: order.index(i["level"]))
    # font-sizeは rem × --font-scale（apply_display_settingsで設定）で表示設定の倍率に追従させる
    parts = [
        "<style>",
        ".key-info-heading{font-size:calc(1.25rem * var(--font-scale, 1));font-weight:700;margin:0.5rem 0;}",
        ".key-info-card{border-left:0.5rem solid;border-radius:0.25rem;padding:0.6rem 0.8rem;margin-bottom:0.5rem;color:#1a1a1a;}",
        ".key-info-level{font-size:calc(0.8rem * var(--font-scale, 1));font-weight:700;}",
        ".key-info-title{font-size:calc(1.1rem * var(--font-scale, 1));font-weight:700;}",
        ".key-info-detail{font-size:calc(1rem * var(--font-scale, 1));}",
        "</style>",
        '<div class="key-info-heading">📌 今すぐ確認</div>',
    ]
    for item in sorted_items:
        level = KEY_INFO_LEVELS[item["level"]]
        icon = html.escape(item["icon"])
        parts.append(
            f'<div class="key-info-card key-info-{item["level"]}" '
            f'style="border-left-color:{level["color"]};background-color:{level["bg"]};">'
            f'<div class="key-info-level">{level["label"]}</div>'
            f'<div class="key-info-title">{icon} {html.escape(item["title"])}</div>'
            f'<div class="key-info-detail">{html.escape(item["detail"])}</div>'
            "</div>"
        )
    st.markdown("".join(parts), unsafe_allow_html=True)

# --- 予算チェック ---
BUDGET_STATUS_STYLES = {
    "over": {"color": "#c62828", "label": "オーバー"},
    "near": {"color": "#f9a825", "label": "80%以上"},
    "ok": {"color": "#2e7d32", "label": "予算内"},
}

def budget_status(estimate_yen, budget_yen):
    """見積と予算から over(100%超) / near(80〜100%) / ok を返す"""
    if budget_yen <= 0:
        return "over" if estimate_yen > 0 else "ok"
    ratio = estimate_yen / budget_yen
    if ratio > 1:
        return "over"
    if ratio >= 0.8:
        return "near"
    return "ok"

def build_budget_rows(estimate, budget):
    """保存/生成時の予算設定と見積から予算チェックの行を作る。比較できなければ []"""
    if not estimate or not isinstance(budget, dict):
        return []
    targets = []
    categories = budget.get("categories")
    if budget.get("breakdown_on") and isinstance(categories, dict):
        for category, _, label, _ in BUDGET_CATEGORIES:
            budget_yen = _parse_yen(categories.get(category, 0))
            targets.append((label, estimate.get(category, 0), budget_yen or 0))
    else:
        total_yen = _parse_yen(budget.get("total"))
        if total_yen is None:
            return []
        targets.append(("💴 合計", sum(estimate.values()), total_yen))
    rows = []
    for label, estimate_yen, budget_yen in targets:
        rows.append({
            "label": label,
            "estimate": estimate_yen,
            "budget": budget_yen,
            "status": budget_status(estimate_yen, budget_yen),
            "over": max(estimate_yen - budget_yen, 0),
        })
    return rows

def budget_over_item(rows):
    """超過行があれば「📌 今すぐ確認」に足す warning カードを返す"""
    over_rows = [r for r in rows if r["status"] == "over"]
    if not over_rows:
        return None
    detail = "・".join(f"{r['label'].split(' ', 1)[-1]} ¥{r['over']:,}" for r in over_rows) + " オーバーの見込み"
    return {"level": "warning", "icon": "💰", "title": "予算オーバーの見込み", "detail": detail}

def render_budget_check(rows):
    """「💰 予算チェック」を横棒グラフで表示する（rowsが空なら何も出さない）"""
    if not rows:
        return
    # font-sizeは rem × --font-scale（apply_display_settingsで設定）で表示設定の倍率に追従させる
    parts = [
        "<style>",
        ".budget-heading{font-size:calc(1.25rem * var(--font-scale, 1));font-weight:700;margin:0.75rem 0 0.5rem;}",
        ".budget-row{margin-bottom:0.75rem;}",
        ".budget-label{font-size:calc(1rem * var(--font-scale, 1));font-weight:700;}",
        ".budget-amount{font-size:calc(0.95rem * var(--font-scale, 1));}",
        ".budget-over{font-size:calc(1rem * var(--font-scale, 1));font-weight:700;}",
        ".budget-track{height:0.9rem;background:#e0e0e0;border:1px solid #9e9e9e;border-radius:0.45rem;overflow:hidden;margin-top:0.25rem;}",
        ".budget-bar{height:100%;}",
        "</style>",
        '<div class="budget-heading">💰 予算チェック</div>',
    ]
    for row in rows:
        style = BUDGET_STATUS_STYLES[row["status"]]
        if row["budget"] > 0:
            percent = round(row["estimate"] / row["budget"] * 100)
        else:
            percent = 100 if row["estimate"] > 0 else 0
        width = min(percent, 100)
        over_html = (
            f'<div class="budget-over">⚠️ ¥{row["over"]:,} オーバー</div>' if row["status"] == "over" else ""
        )
        parts.append(
            f'<div class="budget-row budget-{row["status"]}">'
            f'<div class="budget-label">{html.escape(row["label"])}</div>'
            f'<div class="budget-amount">見積 ¥{row["estimate"]:,} ／ 予算 ¥{row["budget"]:,}（{percent}%）</div>'
            f'<div class="budget-track"><div class="budget-bar" '
            f'style="width:{width}%;background-color:{style["color"]};"></div></div>'
            f"{over_html}"
            "</div>"
        )
    st.markdown("".join(parts), unsafe_allow_html=True)

def show_plan(plan_text, budget=None):
    """parse_key_info → render_key_info → 予算チェック → 本文表示 の順でプランを表示する"""
    body, items, budget_estimate = parse_key_info(plan_text)
    budget_rows = build_budget_rows(budget_estimate, budget)
    over_item = budget_over_item(budget_rows)
    if over_item:
        items = [over_item] + items
    render_key_info(items)
    render_budget_check(budget_rows)
    st.success(body)

# --- サイドバーの予算設定 ---
def allocate_budget(total_yen):
    """総予算を既定の割合で按分し、1000円単位に丸める（丸めの端数はその他で吸収）"""
    allocation = {}
    for category, _, _, ratio in BUDGET_CATEGORIES:
        allocation[category] = int(total_yen * ratio / 1000 + 0.5) * 1000
    remainder = total_yen - sum(allocation.values())
    allocation["other"] = max(allocation["other"] + remainder, 0)
    return allocation

def on_budget_breakdown_toggle():
    """初めてカテゴリ別をONにしたときだけ、総予算の按分で内訳を初期化する"""
    if st.session_state.budget_breakdown_on and not st.session_state.budget_breakdown_initialized:
        allocation = allocate_budget(BUDGET_LEVEL_YEN[st.session_state.budget_total])
        for category, state_key, _, _ in BUDGET_CATEGORIES:
            st.session_state[state_key] = allocation[category]
        st.session_state.budget_breakdown_initialized = True

def get_budget_settings():
    """サイドバーの現在値を Supabase の budget 列の形にする"""
    breakdown_on = bool(st.session_state.budget_breakdown_on)
    categories = None
    if breakdown_on:
        categories = {category: int(st.session_state[state_key] or 0) for category, state_key, _, _ in BUDGET_CATEGORIES}
    return {
        "total": BUDGET_LEVEL_YEN[st.session_state.budget_total],
        "level": st.session_state.budget_total,
        "breakdown_on": breakdown_on,
        "categories": categories,
    }

def format_budget_context(budget):
    """context_info に入れるカテゴリ別予算の行（OFFなら空文字）"""
    if not budget.get("breakdown_on") or not budget.get("categories"):
        return ""
    lines = [f"      - {label.split(' ', 1)[-1]}: {budget['categories'][category]:,}円" for category, _, label, _ in BUDGET_CATEGORIES]
    return "\n    - カテゴリ別予算（1人あたり・円）:\n" + "\n".join(lines)

def restore_budget_to_sidebar(budget):
    """保存された budget をサイドバーの予算ウィジェットへ戻す（ウィジェット生成前のコールバックから呼ぶ）"""
    if not isinstance(budget, dict):
        return
    level = budget.get("level")
    if level not in BUDGET_LEVEL_YEN:
        total_yen = _parse_yen(budget.get("total"))
        level = next((lv for lv, yen in BUDGET_LEVEL_YEN.items() if yen == total_yen), None)
    if level:
        st.session_state.budget_total = level
    categories = budget.get("categories")
    if isinstance(categories, dict):
        for category, state_key, _, _ in BUDGET_CATEGORIES:
            st.session_state[state_key] = _parse_yen(categories.get(category, 0)) or 0
        st.session_state.budget_breakdown_initialized = True
    st.session_state.budget_breakdown_on = bool(budget.get("breakdown_on")) and isinstance(categories, dict)

def copy_public_plan(plan):
    """公開プランのコピー編集（on_clickで呼ぶのでサイドバー生成前に session_state を書き換えられる）"""
    st.session_state.final_plan = plan.get("plan_content")
    st.session_state.last_plan_a = plan.get("plan_a")
    st.session_state.last_plan_b = plan.get("plan_b")
    st.session_state.plan_budget = plan.get("budget")
    restore_budget_to_sidebar(plan.get("budget"))
    log_event("plan_copied")

# --- 4. UI設定 ---
st.set_page_config(page_title="旅行計画立て直しAI", page_icon="🧳", layout="wide")
apply_display_settings()
st.title("✈️ 旅行計画立て直しAI")
render_display_settings()
st.caption("旅行先での営業時間や天候の変化にも、その場でスムーズに立て直せます")

with st.expander("📖 使い方", expanded=False):
    st.markdown("""
1️⃣ サイドバーに出発地・日時を入力(フライト情報・予算は旅行前にプランを立てる場合の入力がおすすめ)
2️⃣ 行き先欄に「都市名(英語) 滞在日数」を入力(例: Osaka 2)
3️⃣「プラン作成」ボタンでAIが自動作成(理想案→現実チェック→まとめの3段階)
4️⃣ プラン完成後、下のチャット欄で「もっと〇〇入れて」等リクエストするとその場で再調整
5️⃣ 気に入ったプランは保存・共有・公開OK。「みんなのプランを見る」で他の人のプランも閲覧・コピーできる
""")

if not st.session_state.session_logged:
    log_event("session_started")
    st.session_state.session_logged = True

# --- 共有URL経由でのプラン読み込み ---
if "plan_id" in st.query_params and st.session_state.final_plan is None:
    shared_plan = load_plan_by_id(st.query_params["plan_id"])
    if shared_plan:
        st.session_state.final_plan = shared_plan["plan_content"]
        st.session_state.last_plan_a = shared_plan["plan_a"]
        st.session_state.last_plan_b = shared_plan["plan_b"]
        st.session_state.plan_budget = shared_plan.get("budget")
    else:
        st.error("指定されたプランが見つかりませんでした。")

with st.sidebar:
    st.header("🔐 アカウント")
    if st.session_state.user_id:
        st.write(f"ログイン中: {st.session_state.user_email}")
        if st.button("ログアウト"):
            try:
                supabase.auth.sign_out()
            except Exception:
                pass
            st.session_state.user_id = None
            st.session_state.user_email = None
            st.session_state.sb_access_token = None
            st.session_state.sb_refresh_token = None
            st.rerun()
    else:
        login_tab, signup_tab = st.tabs(["ログイン", "新規登録"])
        with login_tab:
            login_email = st.text_input("メールアドレス", key="login_email")
            login_password = st.text_input("パスワード", type="password", key="login_password")
            if st.button("ログイン"):
                try:
                    auth_response = supabase.auth.sign_in_with_password({"email": login_email, "password": login_password})
                    st.session_state.user_id = auth_response.user.id
                    st.session_state.user_email = auth_response.user.email
                    st.session_state.sb_access_token = auth_response.session.access_token
                    st.session_state.sb_refresh_token = auth_response.session.refresh_token
                    st.rerun()
                except Exception as e:
                    st.error(f"ログインに失敗しました: {e}")
        with signup_tab:
            signup_email = st.text_input("メールアドレス", key="signup_email")
            signup_password = st.text_input("パスワード", type="password", key="signup_password")
            if st.button("新規登録"):
                try:
                    auth_response = supabase.auth.sign_up({"email": signup_email, "password": signup_password})
                    if auth_response.session:
                        st.session_state.user_id = auth_response.user.id
                        st.session_state.user_email = auth_response.user.email
                        st.session_state.sb_access_token = auth_response.session.access_token
                        st.session_state.sb_refresh_token = auth_response.session.refresh_token
                        st.rerun()
                    else:
                        st.info("確認メールを送信しました。メール内のリンクを確認後、ログインしてください。")
                except Exception as e:
                    st.error(f"新規登録に失敗しました: {e}")

    st.divider()
    st.header("旅行の条件")
    travel_date = st.date_input("📅 旅行開始日", datetime.date.today())
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][travel_date.weekday()]
    
    # --- 修正点1: 出発地点と時刻を分離 ---
    st.subheader("出発情報")

    st.caption("📍ボタンを押すと現在地から出発地を自動入力できます")
    departure_location = get_current_location(key="departure_geo")

    if departure_location:
        is_new_departure = (
            st.session_state.current_lat != departure_location["lat"]
            or st.session_state.current_lng != departure_location["lng"]
        )
        if is_new_departure:
            st.session_state.current_lat = departure_location["lat"]
            st.session_state.current_lng = departure_location["lng"]
            place_name = reverse_geocode(gmaps, departure_location["lat"], departure_location["lng"])
            if place_name:
                st.session_state.current_location_name = place_name
                st.session_state.departure_loc = place_name
                log_event("location_departure_used")
            st.rerun()

    departure_loc = st.text_input("📍 出発地点", placeholder="例：西宮北口駅", key="departure_loc")
    departure_time = st.time_input("🕒 出発時刻", value=datetime.time(14, 0))
    
    st.divider()
    
    # --- 修正点2: フライト情報のラベルを「到着」「出発」に最適化 ---
    st.subheader("✈️ フライト詳細（自由入力）")
    col1, col2 = st.columns(2)
    with col1:
        f_out_no = st.text_input("往路 便名 ", placeholder="NH1866")
    with col2:
        f_out_time = st.text_input("往路 到着時刻 ", placeholder="14:15")
        st.caption("※現地到着時間")
    
    col3, col4 = st.columns(2)
    with col3:
        f_return_no = st.text_input("復路 便名", placeholder="ANA123")
    with col4:
        f_return_time = st.text_input("復路 出発時刻", placeholder="18:30")
        st.caption("※現地出発時間")
        
    st.divider()
    budget = st.select_slider("総予算", options=BUDGET_LEVELS, key="budget_total")
    total_budget_yen = BUDGET_LEVEL_YEN[budget]
    st.caption(f"目安：¥{total_budget_yen:,}（1人あたり・旅行全体）")
    st.toggle("カテゴリ別に予算を設定する", key="budget_breakdown_on", on_change=on_budget_breakdown_toggle)
    if st.session_state.budget_breakdown_on:
        for _, state_key, label, _ in BUDGET_CATEGORIES:
            st.number_input(f"{label}（円）", min_value=0, step=1000, key=state_key)
        breakdown_diff = sum(st.session_state[state_key] for _, state_key, _, _ in BUDGET_CATEGORIES) - total_budget_yen
        if breakdown_diff > 0:
            st.caption(f"⚠️ 内訳の合計が総予算より¥{breakdown_diff:,}多いです")
        elif breakdown_diff < 0:
            st.caption(f"⚠️ 内訳の合計が総予算より¥{-breakdown_diff:,}少ないです")
    preferences = st.text_area("こだわり")

# 目的地入力のガイダンスを強化
destination = st.text_input("目的地（英語表記で入力）と期間", placeholder="例：kyoto　2泊3日 ※スペースを空けて入力してください")

# --- 5. メイン実行ロジック ---
if st.button("🚀 議論を開始する") and destination:
    log_event("plan_generated")
    dest_parts = destination.replace('　', ' ').split()
    city_name = dest_parts[0] if dest_parts else ""

    with st.status(f"🌐 {city_name}を統合調査中...", expanded=True):
        weather_data = get_weather_info(city_name)
        if weather_data:
            weather_text = weather_data["text"]
            clothing_tip = get_clothing_tip(weather_data["temp"], weather_data["desc"])
        else:
            weather_text = "取得失敗"
            clothing_tip = "情報なし"
        
        diverse_links = search_diverse_links(city_name)
        transit_link = search_transit_status(city_name)
        route_link = generate_google_maps_route(departure_loc, city_name)
        a_images = search_web_assets(f"{city_name} 観光 人気", "images")
        real_data = get_place_details_text(city_name)
        
        st.write(f"✅ 調査完了。現地天候：{weather_text}")

    # 生成時の予算設定を保持（表示・保存・チャット再計画で引き継ぐ）
    st.session_state.plan_budget = get_budget_settings()
    budget_context = format_budget_context(st.session_state.plan_budget)

    # コンテキスト情報の構成を更新
    st.session_state.context_info = f"""
    - 旅行日: {travel_date} ({weekday_ja})
    - 目的地: {destination}
    - 出発地点: {departure_loc}
    - 出発時刻: {departure_time.strftime('%H:%M')}
    - 往路(行きの便): {f_out_no} (現地に {f_out_time} 到着予定)
    - 復路(帰りの便): {f_return_no} (現地を {f_return_time} 出発予定)
    - 予算感: {budget}（総予算の目安：1人あたり・旅行全体で約{total_budget_yen:,}円）{budget_context}
    - ユーザーのこだわり: {preferences}
    - 現地コンディション: {weather_text}
    - 服装アドバイス: {clothing_tip}
    - 運行情報: {transit_link}
    - 地図ルート: {route_link}
    - 参考リンク: \n{diverse_links}
    - 現地店舗詳細: {real_data}
    """

    with st.status("🌸 プラン考案中...", expanded=True):
        a_input = f"{destination}の案を作って。1〜3時間単位のスケジュールで構成して。"
        st.session_state.last_plan_a = ask_agent(ROLES["A"], st.session_state.context_info, a_input)
    
    with st.status("⚡ チェック中...", expanded=True):
        b_input = f"Aの案: {st.session_state.last_plan_a}\n日本語で厳しくチェックして。"
        st.session_state.last_plan_b = ask_agent(ROLES["B"], st.session_state.context_info, b_input)
    
    with st.status("⚖️ 最終調整中...", expanded=True):
        c_input = f"AとBを統合。1〜3時間単位を維持し、冒頭に天気と服装、最後に全てのリンクを掲載して。"
        st.session_state.final_plan = ask_agent(ROLES["C"], st.session_state.context_info, c_input)
        st.balloons()

# --- 最終表示と対話リファイン ---
if st.session_state.final_plan:
    st.divider()
    st.subheader("⚖️ 最終判断（プラン）")
    show_plan(st.session_state.final_plan, st.session_state.plan_budget)

    if st.button("🔗 共有用URLを発行"):
        try:
            st.session_state.share_url = save_plan_and_get_url(
                st.session_state.final_plan,
                st.session_state.last_plan_a,
                st.session_state.last_plan_b,
                st.session_state.plan_budget
            )
            log_event("share_url_created")
        except Exception as e:
            st.error(f"共有用URLの発行に失敗しました: {e}")

    if st.session_state.share_url:
        st.code(st.session_state.share_url)

    if st.session_state.user_id:
        is_public = st.checkbox("🌍 このプランをみんなに公開する", value=False)
        if st.button("💾 このプランを保存"):
            try:
                save_plan_record(
                    destination,
                    travel_date,
                    st.session_state.final_plan,
                    st.session_state.last_plan_a,
                    st.session_state.last_plan_b,
                    st.session_state.user_id,
                    is_public,
                    st.session_state.plan_budget
                )
                log_event("plan_saved")
                if is_public:
                    log_event("plan_published")
                st.success("保存しました！")
            except Exception as e:
                st.error(f"保存に失敗しました: {e}")
    else:
        st.info("ログインすると保存できます。")

    with st.expander("🔍 議論プロセス（旅行計画の詳細）を確認"):
        col_a, col_b = st.columns(2)
        with col_a:
            st.chat_message("assistant", avatar="🌸").markdown("**Agent A (ワクワク担当)**")
            st.write(st.session_state.last_plan_a)
        with col_b:
            st.chat_message("assistant", avatar="⚡").markdown("**Agent B (現実担当)**")
            st.write(st.session_state.last_plan_b)

    user_feedback = st.chat_input("修正案を入力してください（例：お昼はお寿司がいい、もっとゆったり等）")
    if user_feedback:
        with st.status("🔄 プランを再構築中...", expanded=True):
            refine_a = f"修正希望: {user_feedback}\nこれを反映して再提案して。"
            st.session_state.last_plan_a = ask_agent(ROLES["A"], st.session_state.context_info, refine_a)
            refine_b = f"修正案: {st.session_state.last_plan_a}\n日本語で再チェックして。"
            st.session_state.last_plan_b = ask_agent(ROLES["B"], st.session_state.context_info, refine_b)
            refine_c = f"AとBを統合し、天気とリンクを維持して最終案を完成させて。"
            st.session_state.final_plan = ask_agent(ROLES["C"], st.session_state.context_info, refine_c)
            st.rerun()

    st.markdown("---")
    st.caption("📍ボタンを押すと現在地を踏まえて再提案します")
    realtime_location = get_current_location(key="realtime_geo")

    if realtime_location:
        is_new_realtime = (
            st.session_state.last_realtime_lat != realtime_location["lat"]
            or st.session_state.last_realtime_lng != realtime_location["lng"]
        )
        if is_new_realtime:
            st.session_state.last_realtime_lat = realtime_location["lat"]
            st.session_state.last_realtime_lng = realtime_location["lng"]
            lat, lng = realtime_location["lat"], realtime_location["lng"]
            st.session_state.current_lat = lat
            st.session_state.current_lng = lng
            place_name = reverse_geocode(gmaps, lat, lng)
            if place_name:
                st.session_state.current_location_name = place_name

            with st.status("📍 現在地をもとに再提案中...", expanded=True):
                current_weather_data = get_weather_by_coords(lat, lng)
                current_weather_text = current_weather_data["text"] if current_weather_data else "取得失敗"

                realtime_context = f"""
    - 現在地: {place_name or "取得できませんでした"}
    - 現在地の天気: {current_weather_text}
    """
                refine_a = f"現在地情報を踏まえて、現時点からの残りのプランを再提案して。\n{realtime_context}"
                st.session_state.last_plan_a = ask_agent(ROLES["A"], st.session_state.context_info, refine_a)
                refine_b = f"修正案: {st.session_state.last_plan_a}\n日本語で再チェックして。"
                st.session_state.last_plan_b = ask_agent(ROLES["B"], st.session_state.context_info, refine_b)
                refine_c = f"AとBを統合し、天気とリンクを維持して最終案を完成させて。"
                st.session_state.final_plan = ask_agent(ROLES["C"], st.session_state.context_info, refine_c)

            log_event("location_realtime_reprop_used")
            st.rerun()

# --- みんなのプランを見る ---
st.divider()
st.subheader("🌍 みんなのプランを見る")
public_plans = get_public_plans()
if public_plans:
    for p in public_plans:
        label = f"📍 {p.get('destination') or '目的地不明'}｜🗓️ {p.get('travel_date') or '日付不明'}｜投稿日時: {p.get('created_at')}"
        with st.expander(label):
            show_plan(p.get("plan_content"), p.get("budget"))
            col_a, col_b = st.columns(2)
            with col_a:
                st.chat_message("assistant", avatar="🌸").markdown("**Agent A (ワクワク担当)**")
                st.write(p.get("plan_a"))
            with col_b:
                st.chat_message("assistant", avatar="⚡").markdown("**Agent B (現実担当)**")
                st.write(p.get("plan_b"))

            # サイドバーの予算ウィジェットを書き換えるため、on_click（次の実行の前）で処理する
            st.button("📋 このプランをコピーして編集する", key=f"copy_{p['id']}", on_click=copy_public_plan, args=(p,))
else:
    st.caption("まだ公開されているプランはありません。")

# --- 保存済みプラン一覧 ---
st.divider()
st.subheader("📚 保存済みプラン一覧")
if not st.session_state.user_id:
    st.info("ログインすると自分の保存済みプランが見れます。")
else:
    saved_plans = get_saved_plans_for_user(st.session_state.user_id)
    if saved_plans:
        for saved in saved_plans:
            badge = "🌍公開" if saved.get("is_public") else "🔒非公開"
            label = f"{badge}｜📍 {saved.get('destination') or '目的地不明'}｜🗓️ {saved.get('travel_date') or '日付不明'}｜保存日時: {saved.get('created_at')}"
            with st.expander(label):
                show_plan(saved.get("plan_content"), saved.get("budget"))
                col_a, col_b = st.columns(2)
                with col_a:
                    st.chat_message("assistant", avatar="🌸").markdown("**Agent A (ワクワク担当)**")
                    st.write(saved.get("plan_a"))
                with col_b:
                    st.chat_message("assistant", avatar="⚡").markdown("**Agent B (現実担当)**")
                    st.write(saved.get("plan_b"))

                confirm_key = f"confirm_delete_{saved['id']}"
                if confirm_key not in st.session_state:
                    st.session_state[confirm_key] = False

                if not st.session_state[confirm_key]:
                    if st.button("🗑️ 削除", key=f"delete_{saved['id']}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    st.warning("本当に削除しますか？この操作は取り消せません。")
                    col_yes, col_no = st.columns(2)
                    with col_yes:
                        if st.button("はい、削除する", key=f"delete_yes_{saved['id']}"):
                            try:
                                delete_plan(saved["id"])
                                st.session_state[confirm_key] = False
                                st.success("削除しました。")
                                st.rerun()
                            except Exception as e:
                                st.error(f"削除に失敗しました: {e}")
                    with col_no:
                        if st.button("キャンセル", key=f"delete_no_{saved['id']}"):
                            st.session_state[confirm_key] = False
                            st.rerun()
    else:
        st.caption("まだ保存されたプランはありません。")

# --- 利用状況（開発者専用） ---
if st.session_state.user_id and st.session_state.user_email == os.getenv("ADMIN_EMAIL"):
    st.divider()
    st.subheader("📊 利用状況")

    st.write("#### イベント別件数")
    event_counts = get_event_counts()
    if event_counts:
        st.bar_chart(pd.DataFrame({"件数": event_counts}))
    else:
        st.caption("まだログがありません。")

    st.write("#### ファネル（訪問 → プラン生成 → アクション）")
    funnel = get_funnel_counts()
    visited = funnel["visited"]
    generated = funnel["generated"]
    action_taken = funnel["action_taken"]
    generated_rate = f"{generated / visited * 100:.1f}%" if visited else "-"
    action_rate = f"{action_taken / generated * 100:.1f}%" if generated else "-"
    funnel_df = pd.DataFrame({
        "ステップ": ["訪問 (session_started)", "プラン生成 (plan_generated)", "アクション (共有/保存/コピー)"],
        "件数": [visited, generated, action_taken],
        "前段階からの継続率": ["-", generated_rate, action_rate]
    })
    st.dataframe(funnel_df, use_container_width=True, hide_index=True)