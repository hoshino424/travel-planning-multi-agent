import streamlit as st
import os
import datetime
import requests
import json
from openai import OpenAI
from dotenv import load_dotenv
import googlemaps

# 1. 初期設定
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
gmaps = googlemaps.Client(key=os.getenv('GOOGLE_MAPS_API_KEY'))

# --- 検索API (Serper) 関連の関数 ---
def search_flight_info(departure, arrival, date):
    """Serper APIを使って実際のフライト時刻をGoogle検索する"""
    url = "https://google.serper.dev/search"
    
    # 検索クエリを作成（例：2026-05-18 那覇空港 新潟空港 飛行機 直行便 運航時刻）
    query = f"{date} {departure} {arrival} 飛行機 直行便 運航時刻 スケジュール"
    
    payload = json.dumps({"q": query, "gl": "jp", "hl": "ja"})
    headers = {
        'X-API-KEY': os.getenv("SERPER_API_KEY"),
        'Content-Type': 'application/json'
    }

    try:
        response = requests.request("POST", url, headers=headers, data=payload)
        results = response.json()
        
        # 検索結果の「スニペット（要約文）」を上位3件抽出
        search_essentials = ""
        if "organic" in results:
            for item in results["organic"][:3]:
                search_essentials += f"- {item.get('snippet', '')}\n"
        
        return search_essentials if search_essentials else "詳細な運航情報は取得できませんでした。"
    except Exception as e:
        return f"検索エラーが発生しました: {e}"

# --- Google Maps API関連の関数 ---
def get_place_details(place_name):
    """Google Mapsから詳細情報を取得する"""
    try:
        result = gmaps.places(query=place_name)
        if result['status'] == 'OK':
            place_id = result['results'][0]['place_id']
            details = gmaps.place(place_id=place_id, language='ja')
            opening_hours = details['result'].get('opening_hours', {}).get('weekday_text', '情報なし')
            return {
                "name": details['result']['name'],
                "status": "営業中" if details['result'].get('opening_hours', {}).get('open_now') else "閉店中",
                "hours": opening_hours
            }
    except Exception as e:
        return f"検索エラー: {e}"
    return None

def get_place_details_text(place_name):
    """取得した情報をAIが読みやすいテキスト形式に変換する"""
    res = get_place_details(place_name)
    if isinstance(res, dict):
        hours_text = "\n".join(res['hours']) if isinstance(res['hours'], list) else res['hours']
        return f"【Google Mapsの最新情報：{res['name']}】\n現在の状況：{res['status']}\n全曜日の営業時間：\n{hours_text}"
    return f"【Google Maps情報】「{place_name}」の正確な営業時間は取得できませんでした。"

# --- AIエージェント設定 ---
ROLES = {
    "A": """あなたは【旅の理想・ワクワク担当】です。
ユーザーの願いをすべて肯定し、夢のような最高のプランを提案してください。
指定された「旅行日」「出発地点」「出発時刻」と、提示された「フライトの事実データ」に基づき、魅力的な行程を作成してください。""",

    "B": """あなたは【現実の制約・ブレーキ担当】です。
Agent Aが提案したプランに対し、「移動距離」「スケジュール」「予算」「体力」の観点から厳しくダメ出しをしてください。
特に、検索で見つかった【フライトの運航時刻】を無視していないか、空港到着に90分以上の余裕があるか、営業時間は正しいかを徹底的に指摘してください。""",

    "C": """あなたは【納得の合意形成・まとめ担当】です。
Agent Aの『理想的な体験』とAgent Bの『物理的な制約』のバランスが最も取れた『最適解』を導き出してください。
フライト時刻、出発地、時刻という条件を死守し、Google検索とMapsの事実データに基づいた、実現可能な最終スケジュールを提示してください。"""
}

def ask_agent(role_prompt, context, user_input):
    system_content = f"{role_prompt}\n\n【現在の前提条件と事実】\n{context}"
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_input}
        ]
    )
    return response.choices[0].message.content

# --- UI部分 ---
st.set_page_config(page_title="旅行計画マルチエージェント", page_icon="🧳")
st.title("🧳 旅行計画マルチエージェント")
st.write("最新のフライト情報とGoogle Mapsデータに基づき、現実的なプランを提案します。")

# サイドバー設定
with st.sidebar:
    st.header("旅行の条件")
    travel_date = st.date_input("📅 旅行開始日", datetime.date.today())
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][travel_date.weekday()]
    
    departure_loc = st.text_input("📍 出発地点（例：那覇空港）", placeholder="那覇空港")
    departure_time = st.text_input("⏰ 出発時刻", placeholder="13時")
    st.divider()
    budget = st.select_slider("予算イメージ", options=["節約", "標準", "贅沢"])
    preferences = st.text_area("こだわり", placeholder="例：新潟で美味しいお寿司、レンタカー利用")

destination = st.text_input("目的地と期間", placeholder="例：新潟 2泊3日")

# 共通コンテキスト
context_info = f"""
- 旅行日: {travel_date} ({weekday_ja}曜日)
- 目的地: {destination}
- 出発地点: {departure_loc}
- 出発時刻: {departure_time}
- 予算: {budget}
- こだわり: {preferences}
"""

if st.button("議論を開始する") and destination:
    # --- 【新規】ステップ3：フライトの事実確認 ---
    with st.status("✈️ 実際の航空便スケジュールを調査中...", expanded=True):
        flight_fact = search_flight_info(departure_loc, destination, travel_date)
        st.info(f"【検索された運航情報】\n{flight_fact}")

    # --- Agent A ---
    with st.status("🌸 理想担当がプランを考案中...", expanded=True):
        # Aにもフライト情報を共有
        context_with_flight = f"{context_info}\n\n【航空便の事実】\n{flight_fact}"
        plan_a = ask_agent(ROLES["A"], context_with_flight, f"{destination}の最高の旅行プランを作って！")
        st.chat_message("assistant", avatar="🌸").write(plan_a)
    
    # --- Google Mapsでの場所の事実確認 ---
    with st.status(f"🔍 目的地の営業時間を調査中...", expanded=True):
        real_data = get_place_details_text(destination)
        st.info(real_data)
    
    # --- Agent B ---
    with st.status("⚡ 現実担当が反論を準備中...", expanded=True):
        # 検索結果をBに強く意識させる
        b_input = f"""
        Agent Aの提案プラン: {plan_a}
        
        【事実データ（絶対遵守）】
        フライト情報: {flight_fact}
        目的地情報: {real_data}
        
        このデータと旅行日（{weekday_ja}曜日）を照らし合わせ、不可能な移動や時間の矛盾を厳しく指摘してください。
        """
        plan_b = ask_agent(ROLES["B"], context_info, b_input)
        st.chat_message("assistant", avatar="⚡").write(plan_b)
    
    # --- Agent C ---
    with st.status("⚖️ 最終案を調整中...", expanded=True):
        c_input = f"""
        Aの提案：{plan_a}
        Bの反論：{plan_b}
        事実データ：{flight_fact} / {real_data}
        これらを踏まえ、特にフライト時刻を厳守した最終スケジュールを出してください。
        """
        final_judgment = ask_agent(ROLES["C"], context_info, c_input)
        st.divider()
        st.subheader("⚖️ 最終判断（プラン）")
        st.success(final_judgment)

    st.balloons()