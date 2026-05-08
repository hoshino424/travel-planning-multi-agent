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

# --- セッション状態の初期化（追加） ---
if "final_plan" not in st.session_state:
    st.session_state.final_plan = None

# --- 検索API (Serper) 関連の関数 ---
def search_flight_info(departure, arrival, date):
    """Serper APIを使って実際のフライト時刻をGoogle検索する"""
    url = "https://google.serper.dev/search"
    query = f"{date} {departure} {arrival} 飛行機 直行便 運航時刻 スケジュール"
    payload = json.dumps({"q": query, "gl": "jp", "hl": "ja"})
    headers = {
        'X-API-KEY': os.getenv("SERPER_API_KEY"),
        'Content-Type': 'application/json'
    }

    try:
        response = requests.request("POST", url, headers=headers, data=payload)
        results = response.json()
        search_essentials = ""
        if "organic" in results:
            for item in results["organic"][:3]:
                search_essentials += f"- {item.get('snippet', '')}\n"
        return search_essentials if search_essentials else "詳細な運航情報は取得できませんでした。"
    except Exception as e:
        return f"検索エラーが発生しました: {e}"

# --- Google Maps API関連の関数 ---
def get_place_details_text(place_name):
    """取得した情報をAIが読みやすいテキスト形式に変換する"""
    try:
        result = gmaps.places(query=place_name)
        if result['status'] == 'OK':
            place_id = result['results'][0]['place_id']
            details = gmaps.place(place_id=place_id, language='ja')
            name = details['result']['name']
            status = "営業中" if details['result'].get('opening_hours', {}).get('open_now') else "閉店中"
            opening_hours = details['result'].get('opening_hours', {}).get('weekday_text', '情報なし')
            hours_text = "\n".join(opening_hours) if isinstance(opening_hours, list) else opening_hours
            return f"【Google Mapsの最新情報：{name}】\n現在の状況：{status}\n全曜日の営業時間：\n{hours_text}"
    except Exception as e:
        return f"検索エラー: {e}"
    return f"【Google Maps情報】「{place_name}」の正確な営業時間は取得できませんでした。"

# --- AIエージェント設定 ---
ROLES = {
    "A": """あなたは【旅の理想・ワクワク担当】です。
ユーザーの願いをすべて肯定し、夢のような最高のプランを提案してください。
提示された「フライトの確定情報」と「検索データ」に基づき、絶対に乗り遅れない魅力的な行程を作成してください。""",

    "B": """あなたは【現実の制約・ブレーキ担当】です。
Agent Aの提案に対し、「移動距離」「スケジュール」「予算」「体力」の観点から厳しくダメ出しをしてください。
特に、ユーザーが入力した【フライト確定時刻】を1分でも無視していないか、空港到着に90分以上の余裕があるかを徹底的にチェックしてください。""",

    "C": """あなたは【納得の合意形成・まとめ担当】です。
Aの理想とBの制約のバランスが取れた最適解を出してください。
ユーザー入力のフライト時刻は「絶対条件」として死守し、実現可能な最終スケジュールを提示してください。
ユーザーからの追加の修正指示がある場合は、これまでの文脈を維持しつつ柔軟に対応してください。"""
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

# サイドバー設定
with st.sidebar:
    st.header("旅行の条件")
    travel_date = st.date_input("📅 旅行開始日", datetime.date.today())
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][travel_date.weekday()]
    
    departure_loc = st.text_input("📍 出発地点", "那覇空港")
    
    st.divider()
    st.subheader("✈️ フライト詳細（自由入力）")
    st.caption("チケット通りに入力してください。例: 14:15、NH1866")
    
    col1, col2 = st.columns(2)
    with col1:
        f_out_no = st.text_input("往路 便名", placeholder="NH1866")
    with col2:
        f_out_time = st.text_input("往路 時刻", placeholder="14:15")
        
    col3, col4 = st.columns(2)
    with col3:
        f_return_no = st.text_input("復路 便名", placeholder="ANA123")
    with col4:
        f_return_time = st.text_input("復路 時刻", placeholder="18:30")
    
    st.divider()
    budget = st.select_slider("予算イメージ", options=["節約", "標準", "贅沢"])
    preferences = st.text_area("こだわり", placeholder="例：新潟で美味しいお寿司、レンタカー利用")

destination = st.text_input("目的地と期間", placeholder="例：新潟 2泊3日")

# 共通コンテキスト（ユーザー入力フライトを統合）
context_info = f"""
- 旅行日: {travel_date} ({weekday_ja}曜日)
- 目的地: {destination}
- 出発地点: {departure_loc}
- 予算: {budget}
- こだわり: {preferences}
- 確定フライト(往路): {f_out_no} / {f_out_time}
- 確定フライト(復路): {f_return_no} / {f_return_time}
"""

# ボタンクリック時のメイン処理
if st.button("議論を開始する") and destination:
    # 1. フライトの事実確認
    with st.status("✈️ 実際の航空便スケジュールを調査中...", expanded=True):
        flight_fact = search_flight_info(departure_loc, destination, travel_date)
        st.info(f"【検索された運航のヒント】\n{flight_fact}")

    # 2. Agent A
    with st.status("🌸 理想担当がプランを考案中...", expanded=True):
        context_with_facts = f"{context_info}\n\n【調査で見つかった航空便の事実】\n{flight_fact}"
        plan_a = ask_agent(ROLES["A"], context_with_facts, f"{destination}の最高の旅行プランを作って！")
        st.chat_message("assistant", avatar="🌸").write(plan_a)
    
    # 3. Google Maps調査
    with st.status(f"🔍 目的地の営業時間を調査中...", expanded=True):
        real_data = get_place_details_text(destination)
        st.info(real_data)
    
    # 4. Agent B
    with st.status("⚡ 現実担当が反論を準備中...", expanded=True):
        b_input = f"""
        Agent Aの提案: {plan_a}
        
        【絶対遵守のデータ】
        ユーザー入力フライト: {f_out_no}({f_out_time}) / {f_return_no}({f_return_time})
        検索された事実: {flight_fact}
        目的地情報: {real_data}
        
        これらのデータと矛盾（特にフライト時刻の無視）がないか厳しくチェックしてください。
        """
        plan_b = ask_agent(ROLES["B"], context_info, b_input)
        st.chat_message("assistant", avatar="⚡").write(plan_b)
    
    # 5. Agent C
    with st.status("⚖️ 最終案を調整中...", expanded=True):
        c_input = f"""
        Aの提案、Bの反論、そして提供された全ての事実データを統合してください。
        特に、ユーザーが入力したフライト時刻 {f_out_time} と {f_return_time} を軸にした、実現可能なスケジュールを出してください。
        """
        final_judgment = ask_agent(ROLES["C"], context_info, c_input)
        
        # セッション状態に保存（追加）
        st.session_state.final_plan = final_judgment
        st.balloons()

# --- Gemini風：追加修正ループ部分（追加） ---
if st.session_state.final_plan:
    st.divider()
    st.subheader("⚖️ 最終判断（プラン）")
    st.success(st.session_state.final_plan)

    # 修正指示の入力
    user_feedback = st.chat_input("プランへの修正希望があれば教えてください（例：お昼はお寿司がいい、もっとのんびりしたい）")
    
    if user_feedback:
        with st.status("🔄 プランを修正中...", expanded=True):
            refine_prompt = f"""
            現在のプラン:
            {st.session_state.final_plan}
            
            ユーザーからの追加指示:
            {user_feedback}
            
            この指示を反映して、プランをブラッシュアップしてください。
            """
            # Agent Cに再依頼
            updated_plan = ask_agent(ROLES["C"], context_info, refine_prompt)
            # セッション状態を更新
            st.session_state.final_plan = updated_plan
            # 画面をリロードして反映
            st.rerun()