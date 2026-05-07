import streamlit as st
import os
import datetime  # 追加：日付操作用
from openai import OpenAI
from dotenv import load_dotenv
import googlemaps

# 1. 初期設定
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
gmaps = googlemaps.Client(key=os.getenv('GOOGLE_MAPS_API_KEY'))

# --- Google Maps API関連の関数 ---
def get_place_details(place_name):
    """Google Mapsから詳細情報を取得する"""
    try:
        # 1. 場所を検索
        result = gmaps.places(query=place_name)
        if result['status'] == 'OK':
            place_id = result['results'][0]['place_id']
            # 2. 詳細情報を取得
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
        # 営業時間のリストを改行でつなげて見やすくする
        hours_text = "\n".join(res['hours']) if isinstance(res['hours'], list) else res['hours']
        return f"【Google Mapsの最新情報：{res['name']}】\n現在の状況：{res['status']}\n全曜日の営業時間：\n{hours_text}"
    return f"【Google Maps情報】「{place_name}」の正確な営業時間は取得できませんでした。"

# --- AIエージェント設定 ---
ROLES = {
    "A": """あなたは【旅の理想・ワクワク担当】です。
ユーザーの願いをすべて肯定し、夢のような最高のプランを提案してください。
指定された「旅行日」「出発地点」「出発時刻」を起点に、魅力的なスポットを詰め込んだ行程を作成してください。""",

    "B": """あなたは【現実の制約・ブレーキ担当】です。
Agent Aが提案したプランに対し、「出発地点からの移動距離」「タイムスケジュール」「予算」「体力」の観点から厳しくダメ出しをしてください。
特に、指定された【旅行日（曜日）】と【Google Mapsの最新情報】に記載されている営業時間が矛盾していないか、現実的に指摘してください。""",

    "C": """あなたは【納得の合意形成・まとめ担当】です。
Agent Aの『理想的な体験』とAgent Bの『物理的な制約』のバランスが最も取れた『最適解』を導き出してください。
旅行日、出発地、時刻という条件を死守し、Google Mapsの事実データに基づいた、絶対に実現可能な最終スケジュールを提示してください。"""
}

def ask_agent(role_prompt, context, user_input):
    system_content = f"{role_prompt}\n\n【現在の前提条件】\n{context}"
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
st.write("日付とGoogle Mapsの最新データに基づいた、正確なプランを提案します。")

# サイドバー設定
with st.sidebar:
    st.header("旅行の条件")
    
    # 追加：日付入力機能（デフォルトは今日）
    travel_date = st.date_input("📅 旅行開始日", datetime.date.today())
    # 曜日を日本語で取得
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][travel_date.weekday()]
    
    departure_loc = st.text_input("📍 出発地点", placeholder="西宮北口駅")
    departure_time = st.text_input("⏰ 出発時刻", placeholder="13時")
    st.divider()
    budget = st.select_slider("予算イメージ", options=["節約", "標準", "贅沢"])
    preferences = st.text_area("こだわり", placeholder="例：映えスポット、歩きすぎない、海が見えるカフェ")

destination = st.text_input("目的地と期間", placeholder="例：奈良公園 近くのカフェ巡り")

# 共有コンテキスト（AIに渡す情報に日付と曜日を追加）
context_info = f"""
- 旅行日: {travel_date} ({weekday_ja}曜日)
- 目的地: {destination}
- 出発地点: {departure_loc}
- 出発時刻: {departure_time}
- 予算: {budget}
- こだわり: {preferences}
"""

if st.button("議論を開始する") and destination:
    # --- Agent A ---
    with st.status("🌸 理想担当がプランを考案中...", expanded=True):
        plan_a = ask_agent(ROLES["A"], context_info, f"{destination}の最高の旅行プランを作って！")
        st.chat_message("assistant", avatar="🌸").write(plan_a)
    
    # --- Google Mapsでの事実確認ステップ ---
    with st.status(f"🔍 {travel_date}の営業時間を調査中...", expanded=True):
        real_data = get_place_details_text(destination)
        st.info(real_data)
    
    # --- Agent B ---
    with st.status("⚡ 現実担当が反論を準備中...", expanded=True):
        b_input = f"""
        Agent Aの提案プラン: {plan_a}
        
        【Google Mapsの事実データ】
        {real_data}
        
        このデータと旅行日（{weekday_ja}曜日）を照らし合わせ、特に営業時間の矛盾や移動の無理を厳しく指摘してください。
        """
        plan_b = ask_agent(ROLES["B"], context_info, b_input)
        st.chat_message("assistant", avatar="⚡").write(plan_b)
    
    # --- Agent C ---
    with st.status("⚖️ 最終案を調整中...", expanded=True):
        final_judgment = ask_agent(ROLES["C"], context_info, f"Aの提案：{plan_a}\nBの反論：{plan_b}\n事実データ：{real_data}\nこれらを踏まえ、納得感のある最終スケジュールを出してください。")
        st.divider()
        st.subheader("⚖️ 最終判断（プラン）")
        st.success(final_judgment)

    st.balloons()