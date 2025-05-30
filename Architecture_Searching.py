import streamlit as st
import google.generativeai as genai
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
import json
import os
from datetime import datetime, timedelta
import requests
import urllib.parse
import pandas as pd
import plotly.graph_objects as go
import yfinance as yf
import random
from apify_client import ApifyClient

# Streamlit 앱 설정
st.set_page_config(page_title="Searching Architecture", page_icon="🏗️", layout="wide")

# API 키 설정
genai.configure(api_key=st.secrets["GOOGLE_AI_STUDIO_API_KEY"])
YOUTUBE_API_KEYS = [
    st.secrets["YOUTUBE_API_KEY1"],
    st.secrets["YOUTUBE_API_KEY2"],
    st.secrets["YOUTUBE_API_KEY3"],
    st.secrets["YOUTUBE_API_KEY4"]
]
APIFY_API_KEY = st.secrets["APIFY_API_KEY"]
apify_client = ApifyClient(APIFY_API_KEY)

# 건축/건설 도메인별 키워드 정의
ARCH_DOMAINS = {
    "건축계획": ["건축", "설계", "건축디자인", "공공건축", "패시브하우스", "제로에너지"],
    "도시재생": ["도시재생", "리모델링", "재건축", "재개발", "노후 건축물", "역세권 개발"],
    "건설기술": ["스마트건설", "BIM", "드론건설", "모듈러", "건설로봇", "3D프린팅 건축"],
    "건축정책": ["건축법", "건축 규제", "건설안전", "에너지 인증제", "녹색건축"],
    "친화경건축": ["친환경건축", "그린빌딩", "ESG 건축", "LEED", "제로에너지건축"]
}

MAJOR_PROJECTS = [
    "세운재정비촉진지구", "광운대 역세권 개발", "송도 국제도시", "용산국제업무지구", "위례 신도시"
]

# 전역 변수로 현재 사용 중인 API 키 인덱스 추적
current_api_key_index = 0

# 뉴스 검색 함수 (Serp API 사용)
def search_news(domain, additional_query, published_after, max_results=10):
    global current_api_key_index
    
    # API 키 번갈아 사용
    api_keys = [st.secrets["SERP_API_KEY1"], st.secrets["SERP_API_KEY2"]]
    api_key = api_keys[current_api_key_index]
    current_api_key_index = (current_api_key_index + 1) % len(api_keys)
    
    keywords = " OR ".join(ARCH_DOMAINS[domain])
    
    if additional_query:
        query = f"({keywords}) AND ({additional_query})"
    else:
        query = keywords
    
    encoded_query = urllib.parse.quote(query)
    
    url = f"https://serpapi.com/search.json?q={encoded_query}&tbm=nws&api_key={api_key}&num={max_results}&sort=date"
    
    if published_after:
        url += f"&tbs=qdr:{published_after}"
    
    response = requests.get(url)
    news_data = response.json()
    articles = news_data.get('news_results', [])
    
    unique_articles = []
    seen_urls = set()
    for article in articles:
        if article['link'] not in seen_urls:
            unique_articles.append({
                'title': article.get('title', ''),
                'source': {'name': article.get('source', '')},
                'description': article.get('snippet', ''),
                'url': article.get('link', ''),
                'content': article.get('snippet', '')
            })
            seen_urls.add(article['link'])
        if len(unique_articles) == max_results:
            break
    
    return unique_articles

# YouTube 검색 함수
def search_videos_with_transcript(domain, additional_query, published_after, max_results=10):
    try:
        YOUTUBE_API_KEY = random.choice(YOUTUBE_API_KEYS)
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
        keywords = " OR ".join(ARCH_DOMAINS[domain])
        query = f"({keywords}) {additional_query}".strip()
        
        # st.write(f"검색 쿼리: {query}")  # 디버깅용 로그
        
        request = youtube.search().list(
            q=query,
            type='video',
            part='id,snippet',
            order='relevance',
            publishedAfter=published_after,
            maxResults=max_results
        )
        response = request.execute()

        videos_with_transcript = []
        for item in response['items']:
            video_id = item['id']['videoId']
            # if get_video_transcript(video_id):  # 자막 있는 영상만 필터링
            videos_with_transcript.append(item)
        
        # st.write(f"자막이 있는 비디오 수: {len(videos_with_transcript)}")  # 디버깅용 로그
        
        return videos_with_transcript[:max_results], len(response['items'])
    except Exception as e:
        st.error(f"YouTube 검색 중 오류 발생: {str(e)}")
        return [], 0


# 조회 기간 선택 함수
def get_published_after(option):
    today = datetime.utcnow() + timedelta(hours=9)  # UTC 시간을 KST로 변환 (+9시간)
    if option == "최근 1일":
        return (today - timedelta(days=1)).isoformat("T") + "Z"
    elif option == "최근 1주일":
        return (today - timedelta(weeks=1)).isoformat("T") + "Z"
    elif option == "최근 1개월":
        return (today - timedelta(weeks=4)).isoformat("T") + "Z"
    elif option == "최근 3개월":
        return (today - timedelta(weeks=12)).isoformat("T") + "Z"
    elif option == "최근 6개월":
        return (today - timedelta(weeks=24)).isoformat("T") + "Z"
    elif option == "최근 1년":
        return (today - timedelta(weeks=52)).isoformat("T") + "Z"
    else:
        return None  # 이 경우 조회 기간 필터를 사용하지 않음

# 자막 가져오기 함수
def get_video_transcript(video_id):
    try:
        # 1. youtube-transcript-api를 사용하여 자막 가져오기 시도
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['ko', 'en'])
        transcript_text = ' '.join([entry['text'] for entry in transcript_list])
        return transcript_text
    except (TranscriptsDisabled, NoTranscriptFound):
        # 자막이 없거나 비활성화된 경우 Apify 사용
        pass

    # 2. Apify를 사용하여 자막 가져오기 시도
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        run_input = {
            "startUrls": [video_url]
        }
        run = apify_client.actor("topaz_sharingan/Youtube-Transcript-Scraper-1").call(run_input=run_input)
        for item in apify_client.dataset(run["defaultDatasetId"]).iterate_items():
            if item.get("transcript"):
                return item["transcript"]
    except Exception as e:
        pass

    return None


# 비디오 설명과 댓글 정보 가져오기 함수
def get_video_info(video_id):
    try:
        YOUTUBE_API_KEY = random.choice(YOUTUBE_API_KEYS)
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
        
        # 비디오 설명 가져오기
        video_request = youtube.videos().list(
            part="snippet",
            id=video_id
        )
        video_response = video_request.execute()
        description = video_response['items'][0]['snippet']['description'] if video_response['items'] else None
        
        # 댓글 가져오기
        comments_request = youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            textFormat="plainText",
            maxResults=30  # 상위 30개 댓글 가져오기
        )
        comments_response = comments_request.execute()
        comments = [item['snippet']['topLevelComment']['snippet']['textDisplay'] for item in comments_response['items']]
        
        return {
            'description': description,
            'comments': comments
        }
    except Exception as e:
        pass
        return None

# YouTube 영상 요약 함수
def summarize_video(video_id, video_title):
    try:
        transcript = get_video_transcript(video_id)
        video_info = get_video_info(video_id)
        
        if not transcript and not video_info:
            return "비디오 정보를 가져올 수 없어 요약할 수 없습니다."
        
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        content = f"제목: {video_title}\n\n"
        
        if transcript:
            content += f"자막 내용:\n{transcript}\n\n"
        
        if video_info:
            if video_info.get('description'):
                content += f"비디오 설명:\n{video_info['description']}\n\n"
            
            if video_info.get('comments'):
                content += "주요 댓글:\n"
                for comment in video_info['comments']:
                    content += f"- {comment}\n"
                content += "\n"
        
        prompt = f"""다음 YouTube 영상의 정보를 바탕으로 가독성 있는 한 페이지의 보고서 형태로 요약하세요. 최종 결과는 한국어로 작성해주세요. 자막 내용이 메인 정보이고, 비디오 설명과 주요 댓글은 참고 정보입니다.

보고서 구조:
1. 영상 개요
2. 주요 내용
3. 시청자 반응 (댓글 기반)
4. 결론 및 시사점
영상 정보:
{content}"""
        response = model.generate_content(prompt)
        if not response or not response.parts:
            feedback = response.prompt_feedback if response else "No response received."
            return f"요약 중 오류가 발생했습니다: {feedback}"
        summary = response.text
        return summary
    except Exception as e:
        return f"요약 중 오류가 발생했습니다: {str(e)}"


# 뉴스 기사 종합 분석 함수
def analyze_news_articles(articles):
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        # 모든 기사의 제목과 내용을 하나의 문자열로 결합
        all_articles = "\n\n".join([f"제목: {article['title']}\n내용: {article['content']}" for article in articles])
        
        prompt = f"""
다음은 특정 주제에 관한 여러 뉴스 기사의 제목과 내용입니다. 이 기사들을 종합적으로 분석하여 가독성 있는 한 페이지의 보고서를 다음 형식을 참고하여 작성해주세요:

1. 주요 이슈 요약 (3-5개의 핵심 포인트)
2. 상세 분석 (각 주요 이슈에 대한 심층 설명)
3. 다양한 관점 (기사들에서 나타난 서로 다른 의견이나 해석)
4. 시사점 및 향후 전망

보고서는 한국어로 작성해주세요. 분석 시 객관성을 유지하고, 편향된 의견을 제시하지 않도록 주의해주세요.

기사 내용:
{all_articles}
"""
        response = model.generate_content(prompt)

        if not response or not response.parts:
            feedback = response.prompt_feedback if response else "No response received."
            return f"분석 중 오류가 발생했습니다: {feedback}"

        analysis = response.text
        return analysis
    except Exception as e:
        return f"분석 중 오류가 발생했습니다: {str(e)}"



# 파일로 다운로드할 수 있는 함수
def download_summary_file(summary_text, file_name="summary.txt"):
    st.download_button(
        label="💾 다운로드",
        data=summary_text,
        file_name=file_name,
        mime="text/plain"
    )

# 검색 실행 함수 정의
def execute_search():
    st.session_state['search_executed'] = True
    source = st.session_state.get('source')
    if source in ["YouTube", "뉴스"]:
        published_after = get_published_after(st.session_state['period'])
        
        if source == "YouTube":
            # YouTube 영상 검색
            with st.spinner(f"{source}를 검색하고 있습니다..."):
                videos, total_video_results = search_videos_with_transcript(
                    st.session_state['domain'], 
                    st.session_state['additional_query'], 
                    published_after)
                st.session_state.search_results = {'videos': videos, 'news': [], 'financial_info': {}}
                st.session_state.total_results = total_video_results
                st.session_state.summary = ""  # YouTube 검색 시 요약 초기화
        
        elif source == "뉴스":
            # 뉴스 검색 및 자동 분석
            with st.spinner(f"{source}를 검색하고 있습니다..."):
                news_articles = search_news(
                    st.session_state['domain'], 
                    st.session_state['additional_query'], 
                    published_after, 
                    max_results=10)
                total_news_results = len(news_articles)
                st.session_state.search_results = {'videos': [], 'news': news_articles, 'financial_info': {}}
                st.session_state.total_results = total_news_results
                
                # 뉴스 기사 자동 분석
                with st.spinner("뉴스 기사를 종합 분석 중입니다..."):
                    st.session_state.summary = analyze_news_articles(news_articles)
        
        if not st.session_state.total_results:
            st.warning(f"{source}에서 결과를 찾을 수 없습니다. 다른 도메인이나 검색어로 검색해보세요.")


# 세션 상태 초기화
if 'search_executed' not in st.session_state:
    st.session_state['search_executed'] = False
if 'search_results' not in st.session_state:
    st.session_state['search_results'] = {'videos': [], 'news': []}
    st.session_state['total_results'] = 0
if 'summary' not in st.session_state:
    st.session_state['summary'] = ""

# Streamlit 앱
st.markdown('<h1>🤖 건축/건설 AI 서비스 플랫폼 <span style="color:red">AI</span>senet</h1>', unsafe_allow_html=True)
st.markdown("이 서비스는 선택한 건축/건설설 도메인에 대한 YouTube 영상, 뉴스를 검색하고 AI를 이용해 분석 정보를 제공합니다. 좌측 사이드바에서 검색 조건을 선택하고 검색해보세요.")

# 검색이 실행되지 않았을 때만 이미지 표시
if not st.session_state['search_executed']:
    st.image("https://raw.githubusercontent.com/Chojueun/Architecture_Searching/main/cover.png")

# 사이드바에 검색 조건 배치
with st.sidebar:
    st.header("검색 조건")
    source = st.radio("검색할 채널을 선택하세요:", ("YouTube", "뉴스"), key='source')
    domain = st.selectbox("건축/건설 도메인 선택", list(ARCH_DOMAINS.keys()), key='domain')
    additional_query = st.text_input("추가 검색어 (선택 사항)", key="additional_query")
    period = st.selectbox("조회 기간", ["모두", "최근 1일", "최근 1주일", "최근 1개월", "최근 3개월", "최근 6개월", "최근 1년"], index=2, key='period')
    st.button("검색 실행", on_click=execute_search)

# 검색 결과 표시
if st.session_state['search_executed']:
    source = st.session_state['source']

    if source == "YouTube":
        st.subheader(f"🎦 검색된 YouTube 영상")
        for video in st.session_state.search_results['videos']:
            col1, col2 = st.columns([1, 2])
            with col1:
                st.image(video['snippet']['thumbnails']['medium']['url'], use_container_width=True)
            with col2:
                st.subheader(video['snippet']['title'])
                st.markdown(f"**채널명:** {video['snippet']['channelTitle']}")
                st.write(video['snippet']['description'])
                video_url = f"https://www.youtube.com/watch?v={video['id']['videoId']}"
                st.markdown(f"[영상 보기]({video_url})")
                
                video_id = video['id']['videoId']
                video_title = video['snippet']['title']
                if st.button(f"📋 요약 보고서 요청", key=f"summarize_{video_id}"):
                    with st.spinner("영상을 요약하는 중..."):
                        summary = summarize_video(video_id, video_title)
                        st.session_state.summary = summary
            st.divider()
    
    elif source == "뉴스":
        st.subheader(f"📰 검색된 뉴스 기사")
        for i, article in enumerate(st.session_state.search_results['news']):
            st.subheader(article['title'])
            st.markdown(f"**출처:** {article['source']['name']}")
            st.write(article['description'])
            st.markdown(f"[기사 보기]({article['url']})")
            st.divider()
    
    # 요약 결과 표시 및 다운로드 버튼
    st.markdown('<div class="fixed-footer">', unsafe_allow_html=True)
    col1, col2 = st.columns([0.85, 0.15])  # 열을 비율로 분할
    with col1:
        if source == "YouTube":
            st.subheader("📋 영상 요약 보고서")
        elif source == "뉴스":
            st.subheader("📋 뉴스 종합 분석 보고서")

    with col2:
        if st.session_state.summary:
            download_summary_file(st.session_state.summary)
    
    if st.session_state.summary:
        st.markdown(st.session_state.summary, unsafe_allow_html=True)
    else:
        st.write("검색 결과에서 요약할 항목을 선택하거나, 검색 조건을 다시 확인해보세요.")
    st.markdown('</div>', unsafe_allow_html=True)

# 주의사항 및 안내
st.sidebar.markdown("---")
st.sidebar.markdown("**안내사항:**")
st.sidebar.markdown("- 이 서비스는 Google AI Studio API, YouTube Data API, Google Search API, Yahoo Finance를 사용합니다.")
st.sidebar.markdown("- 검색 결과의 품질과 복잡도에 따라 처리 시간이 달라질 수 있습니다.")
st.sidebar.markdown("- 저작권 보호를 위해 개인적인 용도로만 사용해주세요.")
