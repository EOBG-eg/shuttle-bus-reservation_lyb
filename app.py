from flask import Flask, render_template, request, redirect, url_for, flash, session
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

app = Flask(__name__)
# 세션 암호화를 위한 시크릿 키 설정
# (GitHub 공개용 수정) 비밀값을 코드에 적지 않고 환경변수에서 읽는다. 없으면 실행할 때마다 무작위로 만든다
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
DATABASE = 'shuttle.db'

def get_db():
    # SQLite DB 연결 및 데이터 행을 딕셔너리처럼 접근할 수 있도록 row_factory 설정
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def log_history(db, student_id, campus, region, direction, time_slot, seat_number, action):
    # [감사 로그(Audit Trail)] 예약, 취소, 노쇼, 대기열 승격 등 모든 트랜잭션의 이력을 타임스탬프와 함께 기록
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.execute('''INSERT INTO history (student_id, campus, region, direction, time_slot, seat_number, action, created_at) 
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
               (student_id, campus, region, direction, time_slot, seat_number, action, now))

def get_delayed_time(time_str, delay_mins):
    # 입력된 기준 시간('HH:MM')에 관리자가 설정한 지연 시간(분)을 더하여 새로운 시간 반환
    if not delay_mins: return time_str
    t = datetime.strptime(time_str, '%H:%M')
    t += timedelta(minutes=delay_mins)
    return t.strftime('%H:%M')

def get_user_status(db, student_id):
    # [사용자 상태 확인] 학생의 노쇼 누적 횟수 및 예약 정지(블랙리스트) 기한 조회
    user = db.execute('SELECT * FROM users WHERE student_id = ?', (student_id,)).fetchone()
    if not user:
        # 최초 접속 시 users 테이블에 기본값(노쇼 0회)으로 자동 등록 (회원가입 절차 간소화)
        db.execute('INSERT INTO users (student_id, noshow_count) VALUES (?, 0)', (student_id,))
        db.commit()
        return {'noshow_count': 0, 'restricted_until': None}
    return dict(user)

def init_db():
    # [데이터 무결성 제어] 시스템 구동 시 DB 테이블을 초기화하고 UNIQUE 제약을 통해 데이터 정합성 보장
    with app.app_context():
        db = get_db()
        # 1. 사용자 테이블: 패널티 누적 및 정지 기한 관리
        db.execute('''CREATE TABLE IF NOT EXISTS users (student_id TEXT PRIMARY KEY, noshow_count INTEGER DEFAULT 0, restricted_until DATETIME)''')
        
        # 2. 즐겨찾기 테이블: 동일 노선 중복 즐겨찾기 방지 (UNIQUE 제약)
        db.execute('''CREATE TABLE IF NOT EXISTS favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT, campus TEXT, region TEXT, direction TEXT, time_slot TEXT, UNIQUE(student_id, campus, region, direction, time_slot))''')
        
        # 3. 배차 스케줄 테이블: 복합 기본키(PK)를 사용하여 중복 배차 방지
        db.execute('''CREATE TABLE IF NOT EXISTS shuttles (campus TEXT, region TEXT, direction TEXT, time_slot TEXT, capacity INTEGER, delay_minutes INTEGER DEFAULT 0, PRIMARY KEY (campus, region, direction, time_slot))''')
        
        # 4. 예약 테이블: [중요] Race Condition 방어 로직
        # UNIQUE(student_id) -> 1학생 1좌석 독점 방지
        # UNIQUE(campus, region, direction, time_slot, seat_number) -> 다중 접속 시 동일 좌석 초과 예약(Overbooking) 원천 차단
        db.execute('''CREATE TABLE IF NOT EXISTS reservations (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT, campus TEXT, region TEXT, direction TEXT, time_slot TEXT, seat_number INTEGER, UNIQUE(student_id), UNIQUE(campus, region, direction, time_slot, seat_number))''')
        
        # 5. 대기열 테이블: 선입선출(FIFO) 기반 대기열 관리를 위한 생성 시간(created_at) 포함
        db.execute('''CREATE TABLE IF NOT EXISTS waitlists (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT, campus TEXT, region TEXT, direction TEXT, time_slot TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE(student_id))''')
        
        # 6. 이력 테이블: 예약, 취소, 관리자 제어 등 모든 시스템 로그 기록
        db.execute('''CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT, campus TEXT, region TEXT, direction TEXT, time_slot TEXT, seat_number INTEGER, action TEXT, created_at DATETIME)''')
        
        # 초기 배차 데이터 셋업 (테이블이 비어있을 경우에만 기본 시간표 렌더링)
        if db.execute('SELECT COUNT(*) FROM shuttles').fetchone()[0] == 0:
            def insert_shuttle(c, r, d, t):
                db.execute("INSERT INTO shuttles (campus, region, direction, time_slot, capacity, delay_minutes) VALUES (?, ?, ?, ?, ?, 0)", (c, r, d, t, 45))
            # 메트로폴(양주) 캠퍼스 기본 스케줄
            for t in ['08:50', '09:50', '10:50']: insert_shuttle('메트로폴(양주)', '도봉산역', '등교', t)
            for t in ['16:30', '17:30', '18:30', '19:00']: insert_shuttle('메트로폴(양주)', '도봉산역', '하교', t)
            for t in ['08:00', '08:45', '08:50', '09:10', '09:50', '10:30', '11:10']: insert_shuttle('메트로폴(양주)', '양주역', '등교', t)
            for t in ['13:30', '14:00', '14:30', '15:30', '16:10', '17:00', '17:40', '17:45', '18:30']: insert_shuttle('메트로폴(양주)', '양주역', '하교', t)
            for t in ['08:50', '09:50']: insert_shuttle('메트로폴(양주)', '의정부 중앙역', '등교', t)
            for t in ['14:00', '15:00']: insert_shuttle('메트로폴(양주)', '의정부 중앙역', '하교', t)
            # 메디컬(원주문막) 캠퍼스 기본 스케줄
            for t in ['08:10', '08:20', '08:30', '08:40', '09:40']: insert_shuttle('메디컬(원주문막)', '원주 직통', '등교', t)
            for t in ['10:30', '11:30', '12:30', '13:30', '14:30', '15:30', '16:30']: insert_shuttle('메디컬(원주문막)', '원주 직통', '하교', t)
        db.commit()

@app.route('/', methods=['GET', 'POST'])
def index():
    # [로그인 세션 처리]
    if request.method == 'POST':
        student_id = request.form.get('student_id')
        if student_id: session['student_id'] = student_id.strip()
        return redirect(url_for('index'))

    student_id = session.get('student_id')
    db = get_db()
    current_time = datetime.now().strftime('%H:%M')
    
    # [오토 클린업 (Auto-Cleanup)] 당일 무한 순환 로직
    # 현재 시간과 지연 시간이 적용된 마감 시간을 비교하여, 이미 출발한 버스의 예약 및 대기열 데이터를 DB에서 자동 삭제
    shuttles_data = db.execute('SELECT * FROM shuttles ORDER BY time_slot ASC').fetchall()
    for s in shuttles_data:
        delayed_time = get_delayed_time(s['time_slot'], s['delay_minutes'])
        if delayed_time < current_time:
            db.execute('DELETE FROM reservations WHERE campus=? AND region=? AND direction=? AND time_slot=?', (s['campus'], s['region'], s['direction'], s['time_slot']))
            db.execute('DELETE FROM waitlists WHERE campus=? AND region=? AND direction=? AND time_slot=?', (s['campus'], s['region'], s['direction'], s['time_slot']))
    db.commit()
    
    my_reservation, my_waitlist, user_status, fav_shuttles = None, None, None, []
    my_favorites_set = set()
    restriction_info = None 
    
    if student_id:
        user_status = get_user_status(db, student_id)
        
        # [패널티 자동 복구 로직] 정지 기한 만료 여부 확인
        if user_status['restricted_until']:
            restricted_date = datetime.strptime(user_status['restricted_until'], '%Y-%m-%d %H:%M:%S')
            now = datetime.now()
            if now < restricted_date:
                # 정지 기간 중: 프론트엔드 배너 출력을 위한 잔여 시간(일/시간/분) 계산
                diff = restricted_date - now
                days, remainder = diff.days, diff.seconds
                hours, minutes = remainder // 3600, (remainder % 3600) // 60
                left_str = f"{days}일 {hours}시간 {minutes}분" if days > 0 else f"{hours}시간 {minutes}분"
                restriction_info = {'date': restricted_date.strftime('%Y년 %m월 %d일 %H:%M'), 'left': left_str}
            else:
                # 정지 기간 만료: 추가 서버 스크립트 없이 접속 즉시 DB를 자동 업데이트(초기화)하여 예약 자격 복구
                db.execute('UPDATE users SET noshow_count = 0, restricted_until = NULL WHERE student_id = ?', (student_id,))
                db.commit()
                user_status['noshow_count'] = 0
                user_status['restricted_until'] = None

        # 현재 나의 예약 정보 및 대기열 순위(rank) 산출 쿼리
        my_reservation = db.execute('SELECT * FROM reservations WHERE student_id = ?', (student_id,)).fetchone()
        waitlist_query = '''
            SELECT w.campus, w.region, w.direction, w.time_slot, 
                   (SELECT COUNT(*) FROM waitlists w2 WHERE w2.time_slot = w.time_slot AND w2.direction = w.direction AND w2.region = w.region AND w2.campus = w.campus AND w2.id <= w.id) as rank 
            FROM waitlists w WHERE w.student_id = ?
        '''
        my_waitlist = db.execute(waitlist_query, (student_id,)).fetchone()
        
        # 즐겨찾기(Bookmark) 노선 매핑
        fav_records = db.execute('SELECT campus, region, direction, time_slot FROM favorites WHERE student_id = ?', (student_id,)).fetchall()
        my_favorites_set = set([(f['campus'], f['region'], f['direction'], f['time_slot']) for f in fav_records])

    # UI 렌더링에 최적화되도록 캠퍼스 > 노선 > 방향 뎁스로 딕셔너리 구조화
    shuttles_dict = {}
    for s in shuttles_data:
        s_dict = dict(s)
        c, r, d, t = s_dict['campus'], s_dict['region'], s_dict['direction'], s_dict['time_slot']
        reserved_count = db.execute('SELECT COUNT(*) FROM reservations WHERE campus = ? AND region = ? AND direction = ? AND time_slot = ?', (c, r, d, t)).fetchone()[0]
        s_dict['remaining'] = s_dict['capacity'] - reserved_count
        s_dict['delayed_time'] = get_delayed_time(t, s_dict['delay_minutes'])
        s_dict['is_past'] = s_dict['delayed_time'] < current_time
        s_dict['is_favorite'] = (c, r, d, t) in my_favorites_set
        
        if s_dict['is_favorite']: fav_shuttles.append(s_dict)
        if c not in shuttles_dict: shuttles_dict[c] = {}
        if r not in shuttles_dict[c]: shuttles_dict[c][r] = {'등교': [], '하교': []}
        shuttles_dict[c][r][d].append(s_dict)

    return render_template('index.html', student_id=student_id, shuttles_dict=shuttles_dict, my_reservation=my_reservation, my_waitlist=my_waitlist, user_status=user_status, fav_shuttles=fav_shuttles, restriction_info=restriction_info)

@app.route('/favorite/toggle', methods=['POST'])
def toggle_favorite():
    # [즐겨찾기 토글] 기존에 등록되어 있으면 삭제, 없으면 추가하는 단일 엔드포인트
    student_id = session.get('student_id')
    if not student_id: return redirect(url_for('index'))
    campus, region, direction, time_slot = request.form.get('campus'), request.form.get('region'), request.form.get('direction'), request.form.get('time_slot')
    
    db = get_db()
    existing = db.execute('SELECT 1 FROM favorites WHERE student_id=? AND campus=? AND region=? AND direction=? AND time_slot=?', (student_id, campus, region, direction, time_slot)).fetchone()
    if existing:
        db.execute('DELETE FROM favorites WHERE student_id=? AND campus=? AND region=? AND direction=? AND time_slot=?', (student_id, campus, region, direction, time_slot))
    else:
        db.execute('INSERT INTO favorites (student_id, campus, region, direction, time_slot) VALUES (?, ?, ?, ?, ?)', (student_id, campus, region, direction, time_slot))
    db.commit()
    return redirect(url_for('index'))

@app.route('/admin')
def admin_panel():
    # 관리자 대시보드 렌더링
    if session.get('student_id') != 'admin': return redirect(url_for('index'))
    db = get_db()
    shuttles_data = db.execute('SELECT * FROM shuttles ORDER BY time_slot ASC').fetchall()
    admin_shuttles_dict = {}
    for s in shuttles_data:
        s_dict = dict(s)
        c, r, d = s_dict['campus'], s_dict['region'], s_dict['direction']
        if c not in admin_shuttles_dict: admin_shuttles_dict[c] = {}
        if r not in admin_shuttles_dict[c]: admin_shuttles_dict[c][r] = {'등교': [], '하교': []}
        admin_shuttles_dict[c][r][d].append(s_dict)
    return render_template('admin.html', admin_shuttles_dict=admin_shuttles_dict)

@app.route('/admin/add_shuttle', methods=['POST'])
def add_shuttle():
    # [스케줄 동적 관리 - 추가] 학사 일정에 맞춘 버스 일정 동적 생성
    if session.get('student_id') != 'admin': return redirect(url_for('index'))
    campus, region, direction, time_slot = request.form.get('campus'), request.form.get('region'), request.form.get('direction'), request.form.get('time_slot')
    db = get_db()
    try:
        db.execute("INSERT INTO shuttles (campus, region, direction, time_slot, capacity, delay_minutes) VALUES (?, ?, ?, ?, ?, 0)", (campus, region, direction, time_slot, 45))
        db.commit()
    except sqlite3.IntegrityError:
        pass # 이미 동일한 시간표가 있을 경우 튕겨냄
    return redirect(url_for('admin_panel'))

@app.route('/admin/delete_shuttle', methods=['POST'])
def delete_shuttle():
    # [스케줄 동적 관리 - 삭제 및 연쇄 취소(Cascade Delete)]
    if session.get('student_id') != 'admin': return redirect(url_for('index'))
    campus, region, direction, time_slot = request.form.get('campus'), request.form.get('region'), request.form.get('direction'), request.form.get('time_slot')
    db = get_db()
    # 고아 레코드(Orphaned Record) 방지를 위해 해당 버스에 물려있는 모든 예약, 대기, 즐겨찾기 데이터 선제 삭제 처리
    db.execute('DELETE FROM reservations WHERE campus=? AND region=? AND direction=? AND time_slot=?', (campus, region, direction, time_slot))
    db.execute('DELETE FROM waitlists WHERE campus=? AND region=? AND direction=? AND time_slot=?', (campus, region, direction, time_slot))
    db.execute('DELETE FROM favorites WHERE campus=? AND region=? AND direction=? AND time_slot=?', (campus, region, direction, time_slot))
    db.execute('DELETE FROM shuttles WHERE campus=? AND region=? AND direction=? AND time_slot=?', (campus, region, direction, time_slot))
    db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/delay', methods=['POST'])
def set_delay():
    # [실시간 지연 반영]
    if session.get('student_id') != 'admin': return redirect(url_for('index'))
    campus, region, direction, time_slot = request.form.get('campus'), request.form.get('region'), request.form.get('direction'), request.form.get('time_slot')
    delay_minutes = request.form.get('delay_minutes', type=int)
    db = get_db()
    db.execute('UPDATE shuttles SET delay_minutes = ? WHERE campus = ? AND region = ? AND direction = ? AND time_slot = ?', (delay_minutes, campus, region, direction, time_slot))
    db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/passengers')
def admin_passengers():
    # [현장 관제] 해당 스케줄의 실시간 승객 명단(예약자/대기자) 조회
    if session.get('student_id') != 'admin': return redirect(url_for('index'))
    campus, region, direction, time_slot = request.args.get('campus'), request.args.get('region'), request.args.get('direction'), request.args.get('time_slot')
    db = get_db()
    shuttle = db.execute('SELECT * FROM shuttles WHERE campus=? AND region=? AND direction=? AND time_slot=?', (campus, region, direction, time_slot)).fetchone()
    reservations = db.execute('''SELECT r.student_id, r.seat_number, IFNULL(u.noshow_count, 0) as noshow_count FROM reservations r LEFT JOIN users u ON r.student_id = u.student_id WHERE r.campus=? AND r.region=? AND r.direction=? AND r.time_slot=? ORDER BY r.seat_number ASC''', (campus, region, direction, time_slot)).fetchall()
    waitlists = db.execute('SELECT student_id, created_at FROM waitlists WHERE campus=? AND region=? AND direction=? AND time_slot=? ORDER BY id ASC', (campus, region, direction, time_slot)).fetchall()
    return render_template('admin_passengers.html', shuttle=shuttle, reservations=reservations, waitlists=waitlists)

@app.route('/admin/noshow', methods=['POST'])
def mark_noshow():
    # [핵심 로직: 노쇼 통제 및 자원 회수]
    if session.get('student_id') != 'admin': return redirect(url_for('index'))
    student_id = request.form.get('student_id')
    campus, region, direction, time_slot = request.form.get('campus'), request.form.get('region'), request.form.get('direction'), request.form.get('time_slot')
    db = get_db()
    
    # 1. 3진 아웃 패널티 누적 및 정지 기한 부여
    user = get_user_status(db, student_id)
    new_count = user['noshow_count'] + 1
    if new_count >= 3:
        restrict_until = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S') # 7일 정지
        db.execute('UPDATE users SET noshow_count = ?, restricted_until = ? WHERE student_id = ?', (new_count, restrict_until, student_id))
    else:
        db.execute('UPDATE users SET noshow_count = ? WHERE student_id = ?', (new_count, student_id))
    log_history(db, student_id, campus, region, direction, time_slot, None, f"노쇼(패널티 {new_count}회)")
    
    # 2. 노쇼 처리된 학생의 예약을 즉각 무효화(삭제) 처리
    record = db.execute('SELECT * FROM reservations WHERE student_id=? AND campus=? AND region=? AND direction=? AND time_slot=?', (student_id, campus, region, direction, time_slot)).fetchone()
    if record:
        db.execute('DELETE FROM reservations WHERE student_id=? AND campus=? AND region=? AND direction=? AND time_slot=?', (student_id, campus, region, direction, time_slot))
        
        # 3. 빈 자리에 대해 대기 1순위를 추출(FIFO)하여 해당 좌석 번호로 자동 확정 배정(승격)
        next_wait = db.execute('SELECT * FROM waitlists WHERE campus=? AND region=? AND direction=? AND time_slot=? ORDER BY id ASC LIMIT 1', (campus, region, direction, time_slot)).fetchone()
        if next_wait:
            db.execute('DELETE FROM waitlists WHERE id=?', (next_wait['id'],))
            db.execute('INSERT INTO reservations (student_id, campus, region, direction, time_slot, seat_number) VALUES (?, ?, ?, ?, ?, ?)', (next_wait['student_id'], next_wait['campus'], next_wait['region'], next_wait['direction'], next_wait['time_slot'], record['seat_number']))
            
    db.commit()
    return redirect(url_for('admin_passengers', campus=campus, region=region, direction=direction, time_slot=time_slot))

@app.route('/logout')
def logout():
    session.pop('student_id', None)
    return redirect(url_for('index'))

@app.route('/seat')
def seat():
    # 45인승 좌석 배치도 렌더링 라우트
    student_id = session.get('student_id')
    if not student_id: return redirect(url_for('index'))
    campus, region, direction, time_slot = request.args.get('campus'), request.args.get('region'), request.args.get('direction'), request.args.get('time_slot')
    db = get_db()
    shuttle = db.execute('SELECT * FROM shuttles WHERE campus = ? AND region = ? AND direction = ? AND time_slot = ?', (campus, region, direction, time_slot)).fetchone()
    reserved_records = db.execute('SELECT seat_number FROM reservations WHERE campus = ? AND region = ? AND direction = ? AND time_slot = ?', (campus, region, direction, time_slot)).fetchall()
    reserved_seats = [r['seat_number'] for r in reserved_records] # 이미 예약된 좌석 배열 전달
    return render_template('seat.html', student_id=student_id, shuttle=shuttle, reserved_seats=reserved_seats)

@app.route('/reserve', methods=['POST'])
def reserve():
    # [핵심 로직: 좌석 예약 트랜잭션]
    student_id = session.get('student_id')
    if not student_id: return redirect(url_for('index'))
    db = get_db()
    user = get_user_status(db, student_id)
    
    # 보안 강화를 위한 백엔드 단독 접근(API 우회) 시 차단
    if user['restricted_until']:
        restricted_date = datetime.strptime(user['restricted_until'], '%Y-%m-%d %H:%M:%S')
        if datetime.now() < restricted_date:
            return redirect(url_for('index'))
            
    campus, region, direction, time_slot = request.form.get('campus'), request.form.get('region'), request.form.get('direction'), request.form.get('time_slot')
    seat_number, action = request.form.get('seat_number'), request.form.get('action') 
    
    # 1인 1예약 무결성 점검
    if db.execute('SELECT 1 FROM reservations WHERE student_id = ?', (student_id,)).fetchone() or db.execute('SELECT 1 FROM waitlists WHERE student_id = ?', (student_id,)).fetchone():
        flash("이미 예약 또는 대기 중인 내역이 있습니다.")
        return redirect(url_for('index'))

    # 대기열 등록 액션
    if action == 'waitlist':
        db.execute('INSERT INTO waitlists (student_id, campus, region, direction, time_slot) VALUES (?, ?, ?, ?, ?)', (student_id, campus, region, direction, time_slot))
        log_history(db, student_id, campus, region, direction, time_slot, None, "대기등록")
        db.commit()
        return redirect(url_for('index'))

    if not seat_number: return redirect(url_for('seat', campus=campus, region=region, direction=direction, time_slot=time_slot))
    
    # 실제 좌석 예약 시도 (Race Condition 방어)
    try:
        # 다수 학생이 동일 좌석 접근 시, 최초 1인 외에는 IntegrityError 발생
        db.execute('INSERT INTO reservations (student_id, campus, region, direction, time_slot, seat_number) VALUES (?, ?, ?, ?, ?, ?)', (student_id, campus, region, direction, time_slot, int(seat_number)))
        log_history(db, student_id, campus, region, direction, time_slot, int(seat_number), "예약완료")
        db.commit()
    except sqlite3.IntegrityError:
        flash("오류가 발생했습니다. (이미 예약된 좌석)")
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    # [핵심 로직: 자진 취소 및 대기열 승격 (FIFO)]
    student_id = session.get('student_id')
    cancel_type = request.form.get('type')
    db = get_db()
    
    if cancel_type == 'reservation':
        record = db.execute('SELECT campus, region, direction, time_slot, seat_number FROM reservations WHERE student_id = ?', (student_id,)).fetchone()
        if record:
            # 기존 예약 삭제
            db.execute('DELETE FROM reservations WHERE student_id = ?', (student_id,))
            log_history(db, student_id, record['campus'], record['region'], record['direction'], record['time_slot'], record['seat_number'], "예약취소")
            
            # 대기 1순위자 검색 (ORDER BY id ASC LIMIT 1)
            next_wait = db.execute('SELECT * FROM waitlists WHERE campus = ? AND region = ? AND direction = ? AND time_slot = ? ORDER BY id ASC LIMIT 1', (record['campus'], record['region'], record['direction'], record['time_slot'])).fetchone()
            
            # 대기자 자동 승격 처리 (취소된 좌석 번호를 대기자에게 인계)
            if next_wait:
                db.execute('DELETE FROM waitlists WHERE id = ?', (next_wait['id'],))
                db.execute('INSERT INTO reservations (student_id, campus, region, direction, time_slot, seat_number) VALUES (?, ?, ?, ?, ?, ?)', (next_wait['student_id'], next_wait['campus'], next_wait['region'], next_wait['direction'], next_wait['time_slot'], record['seat_number']))
                log_history(db, next_wait['student_id'], next_wait['campus'], next_wait['region'], next_wait['direction'], next_wait['time_slot'], record['seat_number'], "자동예약전환")
            db.commit()
            
    elif cancel_type == 'waitlist':
        record = db.execute('SELECT campus, region, direction, time_slot FROM waitlists WHERE student_id = ?', (student_id,)).fetchone()
        if record:
            db.execute('DELETE FROM waitlists WHERE student_id = ?', (student_id,))
            log_history(db, student_id, record['campus'], record['region'], record['direction'], record['time_slot'], None, "대기취소")
            db.commit()
    return redirect(url_for('index'))

@app.route('/history')
def view_history():
    # 본인의 개인 감사 기록(Audit Trail) 조회 화면
    student_id = session.get('student_id')
    if not student_id: return redirect(url_for('index'))
    db = get_db()
    history_records = db.execute('SELECT * FROM history WHERE student_id = ? ORDER BY created_at DESC', (student_id,)).fetchall()
    return render_template('history.html', student_id=student_id, history_records=history_records)

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)