import streamlit as st
import os
import datetime
import requests
import json
import urllib.parse
from openai import OpenAI
from dotenv import load_dotenv
import googlemaps

# 1. 初期設定
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
gmaps = googlemaps.Client(key=os.getenv('GOOGLE_MAPS_API_KEY'))

# --- セッション状態の初期化 ---
if "final_plan" not in st.session_state:
    st.session_state.final_plan = None
if "last_plan_a" not in st.session_state:
    st.session_state.last_plan_a = None
if "last_plan_b" not in st.session_state:
    st.session_state.last_plan_b = None
if "context_info" not in st.session_state:
    st.session_state.context_info = ""

# --- 2. 関数定義 ---
def get_weather_info(city_name):
    """世界中の都市に対応した天気情報を取得する"""
    api_key = os.getenv("OPENWEATHER_API_KEY")
    # グローバル対応のため ",JP" を削除。都市名だけで世界中を検索します。
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city_name}&appid={api_key}&units=metric&lang=ja"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if data["cod"] == 200:
            weather = data["weather"][0]["description"]
            temp = data["main"]["temp"]
            return {"desc": weather, "temp": temp, "text": f"天気：{weather} / 気温：{temp}°C"}
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
    "B": "あなたは【現実の制約・ブレーキ担当】です。必ず日本語で回答してください。移動距離や天候リスク、営業時間を厳密にチェックし、無理がないか批判的に検討してください。",
    "C": "あなたは【まとめ担当】です。日本語で最終案を出してください。冒頭に『☀️当日のコンディション』、最後に提供されたリンクを全て含む『🔗旅の参考リンク集』を必ず掲載してください。"
}

def ask_agent(role_prompt, context, user_input):
    system_content = f"{role_prompt}\n\n【前提条件】\n{context}"
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": system_content}, {"role": "user", "content": user_input}]
    )
    return response.choices[0].message.content

# --- 4. UI設定 ---
st.set_page_config(page_title="旅行計画マルチエージェント", page_icon="🧳", layout="wide")
st.title("🧳 旅行計画マルチエージェント")

with st.sidebar:
    st.header("旅行の条件")
    travel_date = st.date_input("📅 旅行開始日", datetime.date.today())
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][travel_date.weekday()]
    departure_loc = st.text_input("📍 出発地点", placeholder="例：西宮北口駅 14:00")
    
    st.divider()
    st.subheader("✈️ フライト詳細（自由入力）")
    col1, col2 = st.columns(2)
    with col1:
        f_out_no = st.text_input("往路 便名 ", placeholder="NH1866")
    with col2:
        f_out_time = st.text_input("往路 出発時刻 ", placeholder="14:15")
    
    col3, col4 = st.columns(2)
    with col3:
        f_return_no = st.text_input("復路 便名", placeholder="ANA123")
    with col4:
        f_return_time = st.text_input("復路 出発時刻", placeholder="18:30")
        
    st.divider()
    budget = st.select_slider("予算", options=["節約", "標準", "贅沢"])
    preferences = st.text_area("こだわり")

destination = st.text_input("目的地（英語表記で入力）と期間", placeholder="例：kyoto　2泊3日※目的地と期間はスペース空けて入力")

# --- 5. メイン実行ロジック ---
if st.button("🚀 議論を開始する") and destination:
    # 全角・半角スペース両方で分割できるよう処理
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

    st.session_state.context_info = f"""
    - 旅行日: {travel_date} ({weekday_ja})
    - 目的地: {destination}
    - 出発地点: {departure_loc}
    - 往路: {f_out_no} ({f_out_time}発)
    - 復路: {f_return_no} ({f_return_time}発)
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
    st.success(st.session_state.final_plan)
    
    with st.expander("🔍 議論プロセスを確認"):
        col_a, col_b = st.columns(2)
        with col_a:
            st.chat_message("assistant", avatar="🌸").markdown("**Agent A (ワクワク担当)**")
            st.write(st.session_state.last_plan_a)
        with col_b:
            st.chat_message("assistant", avatar="⚡").markdown("**Agent B (現実担当)**")
            st.write(st.session_state.last_plan_b)

    # フィードバック入力
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