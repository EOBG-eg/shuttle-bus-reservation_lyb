import os
from app import init_db

DB_FILE = 'shuttle.db'

def reset_database():
    # 1. 기존 DB 파일이 있으면 삭제
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
        print("✅ 기존 데이터베이스 파일을 성공적으로 삭제했습니다.")
    else:
        print("지울 데이터베이스 파일이 없습니다.")

    # 2. app.py의 초기화 함수를 불러와서 새로운 구조로 다시 세팅
    init_db()
    print("✅ 새로운 데이터베이스가 초기화되었습니다. 이제 서버를 실행하세요!")

if __name__ == '__main__':
    reset_database()