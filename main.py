import streamlit as st
import os
import datetime
import requests
import json
import urllib.parse  # ルート案内リンク用
from openai import OpenAI
from dotenv import load_dotenv
import googlemaps

# 1. 初期設定
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
gmaps = googlemaps.Client(key=os.getenv('GOOGLE_MAPS_API_KEY'))

# --- セッション状態の初期化（AとBの回答保存用を追加） ---
if "final_plan" not in st.session_state:
    st.session_state.final_plan = None
if "last_plan_a" not in st.session_state:
    st.session_state.last_plan_a = None
if "last_plan_b" not in st.session_state:
    st.session_state.last_plan_b = None

# --- 2. 関数定義 ---

def search_transit_status(area):
    """交通機関の最新情報を取得"""
    url = "https://google.serper.dev/search"
    query = f"{area} 鉄道 運行情報 遅延 混雑 最新"
    payload = json.dumps({"q": query, "gl": "jp", "hl": "ja"})
    headers = {'X-API-KEY': os.getenv("SERPER_API_KEY"), 'Content-Type': 'application/json'}
    try:
        response = requests.post(url, headers=headers, data=payload)
        results = response.json().get("organic", [])
        if results:
            return f"- 🚆 [現在の運行・混雑情報（最新）]({results[0].get('link')})"
    except:
        pass
    return "- 🚆 運行情報の自動取得に失敗しました。"

def generate_google_maps_route(origin, dest):
    """ルート案内リンクを生成"""
    base_url = "https://www.google.com/maps/dir/?api=1"
    origin_enc = urllib.parse.quote(origin)
    dest_enc = urllib.parse.quote(dest)
    route_url = f"{base_url}&origin={origin_enc}&destination={dest_enc}&travelmode=transit"
    return f"- 🗺️ [{origin}から{dest}へのルート案内]({route_url})"

def search_diverse_links(destination):
    """5つの多様なサイトリンクを取得"""
    queries = {
        "official": f"{destination} 観光 公式サイト",
        "blog": f"{destination} 観光 おすすめ ブログ 旅行記",
        "review": f"{destination} 観光スポット 口コミ 評判 レビュー"
    }
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

def search_flight_info(departure, arrival, date):
    query = f"{date} {departure} {arrival} 飛行機 直行便 運航時刻"
    payload = json.dumps({"q": query, "gl": "jp", "hl": "ja"})
    headers = {'X-API-KEY': os.getenv("SERPER_API_KEY"), 'Content-Type': 'application/json'}
    try:
        response = requests.post("https://google.serper.dev/search", headers=headers, data=payload)
        results = response.json()
        search_essentials = ""
        for item in results.get("organic", [])[:3]:
            search_essentials += f"- {item.get('snippet', '')}\n"
        return search_essentials
    except: return "検索エラー"

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
    "A": "あなたは【旅の理想・ワクワク担当】です。夢のプランを提案し、画像やブログ、レビューを活用して魅力を伝えてください。",
    "B": "あなたは【現実の制約・ブレーキ担当】です。移動距離や予算、営業時間を厳しくチェックしてください。Agent Aの提案に具体的なダメ出しをしてください。",
    "C": "あなたは【合意形成・まとめ担当】です。Aの理想とBの制約を統合し、最後に必ず「出発サポート情報」と「参考リンク」をまとめてください。"
}

def ask_agent(role_prompt, context, user_input):
    system_content = f"{role_prompt}\n\n【前提】\n{context}"
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": system_content}, {"role": "user", "content": user_input}]
    )
    return response.choices[0].message.content

# --- 4. UI入力 ---
st.set_page_config(page_title="旅行計画マルチエージェント", page_icon="🧳")
st.title("🧳 旅行計画マルチエージェント")

with st.sidebar:
    st.header("旅行の条件")
    travel_date = st.date_input("📅 旅行開始日", datetime.date.today())
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][travel_date.weekday()]
    departure_loc = st.text_input("📍 出発地点と出発時刻", placeholder="例：西宮北口駅14時")
    
    st.divider()
    st.subheader("✈️ フライト詳細（自由入力）")
    col1, col2 = st.columns(2)
    with col1: f_out_no = st.text_input("空港名　往路 便名", placeholder="神戸空港NH1866")
    with col2: f_out_time = st.text_input("空港名　往路 時刻", placeholder="神戸空港14:15")
    col3, col4 = st.columns(2)
    with col3: f_return_no = st.text_input("空港名　復路 便名", placeholder="神戸空港ANA123")
    with col4: f_return_time = st.text_input("空港名　復路 時刻", placeholder="神戸空港18:30")
    
    st.divider()
    budget = st.select_slider("予算イメージ", options=["節約", "標準", "贅沢"])
    preferences = st.text_area("こだわり", placeholder="例：美味しいお寿司、レンタカー利用")

destination = st.text_input("目的地と期間", placeholder="例：岐阜 2泊3日")

context_info = f"""
- 旅行日: {travel_date} ({weekday_ja}曜日)
- 目的地: {destination}
- 出発地点: {departure_loc}
- 予算: {budget}
- こだわり: {preferences}
- フライト: 往路{f_out_no}({f_out_time}) / 復路{f_return_no}({f_return_time})
"""

# --- 5. メイン実行ロジック ---
if st.button("議論を開始する") and destination:
    with st.status("🌐 調査中...", expanded=True):
        diverse_links = search_diverse_links(destination)
        f_fact = search_flight_info(departure_loc, destination, travel_date)
        a_images = search_web_assets(f"{destination} 観光 人気", "images")
        real_data = get_place_details_text(destination)
        transit_link = search_transit_status(destination)
        route_link = generate_google_maps_route(departure_loc, destination)
        st.write("調査完了。")

    with st.status("🌸 プラン考案中...", expanded=True):
        a_input = f"{destination}の案を作って。画像:\n{a_images}\nリンク:\n{diverse_links}\nフライト:\n{f_fact}"
        st.session_state.last_plan_a = ask_agent(ROLES["A"], context_info, a_input)
    
    with st.status("⚡ チェック中...", expanded=True):
        b_input = f"Aの案: {st.session_state.last_plan_a}\n現地データ: {real_data}"
        st.session_state.last_plan_b = ask_agent(ROLES["B"], context_info, b_input)
    
    with st.status("⚖️ 最終調整中...", expanded=True):
        c_input = f"AとBを統合。以下のリンクを最後に追加せよ:\n{transit_link}\n{route_link}\n{diverse_links}"
        st.session_state.final_plan = ask_agent(ROLES["C"], context_info, c_input)
        st.balloons()

# --- 最終表示と修正ループ ---
if st.session_state.final_plan:
    st.divider()

    # 【新機能】議論プロセスのバー表示
    with st.expander("🔍 エージェントAとBの議論プロセスを確認"):
        col_a, col_b = st.columns(2)
        with col_a:
            st.chat_message("assistant", avatar="🌸").markdown("**Agent A (理想)**")
            st.write(st.session_state.last_plan_a)
        with col_b:
            st.chat_message("assistant", avatar="⚡").markdown("**Agent B (現実)**")
            st.write(st.session_state.last_plan_b)

    st.subheader("⚖️ 最終判断（プラン）")
    st.success(st.session_state.final_plan)

    user_feedback = st.chat_input("修正希望を教えてください")
    if user_feedback:
        with st.status("🔄 エージェントたちが再議論中...", expanded=True):
            # 1. Agent A が修正案を作成
            refine_a = f"現在のプラン: {st.session_state.final_plan}\nユーザーの希望: {user_feedback}\nこれを受けて提案を更新してください。"
            st.session_state.last_plan_a = ask_agent(ROLES["A"], context_info, refine_a)
            
            # 2. Agent B が再チェック
            refine_b = f"Aの修正案: {st.session_state.last_plan_a}\n再度、現実的な観点で厳しくチェックしてください。"
            st.session_state.last_plan_b = ask_agent(ROLES["B"], context_info, refine_b)
            
            # 3. Agent C が最終統合
            refine_c = f"Aの修正案とBの再評価を統合し、完璧な最終回答を出してください。"
            st.session_state.final_plan = ask_agent(ROLES["C"], context_info, refine_c)
            
            st.rerun()