from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime, date, timedelta
import pandas as pd
from io import BytesIO
import os
from functools import wraps
from flask import session
from werkzeug.security import generate_password_hash, check_password_hash
import psutil
import logging
import json

app = Flask(__name__, 
    template_folder='templates')
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///employees.db'
app.config['ADMIN_USERNAME'] = 'admin'  # 管理员用户名
app.config['ADMIN_PASSWORD'] = '123456'  # 管理员密码

# 添加 zip 过滤器到 Jinja2 环境
app.jinja_env.filters['zip'] = zip

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# 配置日志
logging.basicConfig(
    filename='system.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 管理员模型
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(120))
    store_name = db.Column(db.String(120))  # 店铺名称
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # 建立与员工的关系
    employees = db.relationship('Employee', backref='admin', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# 员工模型
class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    position = db.Column(db.String(100))
    admin_id = db.Column(db.Integer, db.ForeignKey('admin.id'), nullable=False)
    
    def __repr__(self):
        return f'<Employee {self.name}>'

    def get_total_unpaid_loans(self):
        unpaid_loans = Loan.query.filter_by(
            employee_id=self.id,
            status='未还'
        ).all()
        return sum(loan.amount for loan in unpaid_loans)

    def get_today_attendance(self):
        today = date.today()
        return Attendance.query.filter_by(
            employee_id=self.id,
            date=today
        ).first()

# 考勤模型
class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False)  # 上班/请假
    hours = db.Column(db.Float, default=8.0)  # 工作时长，默认8小时
    note = db.Column(db.String(200))
    employee = db.relationship('Employee', backref=db.backref('attendances', lazy=True))

# 借款模型
class Loan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    loan_date = db.Column(db.Date, nullable=False)
    repayment_date = db.Column(db.Date)
    status = db.Column(db.String(20), default='未还')
    purpose = db.Column(db.String(200))
    employee = db.relationship('Employee', backref=db.backref('loans', lazy=True))

# 工资结算记录
class SalarySettlement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Float, nullable=False)  # 结算金额
    settlement_date = db.Column(db.Date, nullable=False)  # 结算日期
    note = db.Column(db.String(200))
    employee = db.relationship('Employee', backref=db.backref('salary_settlements', lazy=True))

# 假期记录
class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    name = db.Column(db.String(50), nullable=False)  # 假期名称
    note = db.Column(db.String(200))

# 轮休模型
class Rotation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)  # 轮休开始日期（周一）
    is_working_week = db.Column(db.Boolean, default=True)  # True表示上班周，False表示休息周
    note = db.Column(db.String(200))
    employee = db.relationship('Employee', backref=db.backref('rotations', lazy=True))

# 登录验证装饰器
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('请先登录')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# 超级管理员验证装饰器
def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_super_admin'):
            flash('需要超级管理员权限')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# 管理员操作日志模型
class AdminActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('admin.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    action_type = db.Column(db.String(50), nullable=False)
    details = db.Column(db.String(500))
    admin = db.relationship('Admin', backref=db.backref('activity_logs', lazy=True))

@app.route('/')
@login_required
def index():
    # 获取当前登录的管理员
    username = session.get('username')
    is_super_admin = session.get('is_super_admin', False)
    
    if is_super_admin:
        # 超级管理员视图：显示所有管理员账户
        admins = Admin.query.filter(
            Admin.username != 'superadmin'
        ).order_by(Admin.created_at.desc()).all()
        
        # 为每个管理员计算员工数量
        for admin in admins:
            admin.employee_count = Employee.query.filter_by(admin_id=admin.id).count()
        
        return render_template('index.html',
            admins=admins,
            is_super_admin=True
        )
    else:
        # 普通管理员视图：显示自己的员工
        admin = Admin.query.filter_by(username=username).first()
        if not admin:
            flash('账户异常，请重新登录')
            return redirect(url_for('logout'))
            
        # 获取该管理员的所有员工
        employees = Employee.query.filter_by(admin_id=admin.id).all()
        total_employees = len(employees)
        
        # 获取今日考勤数据
        today = date.today()
        first_day = date(today.year, today.month, 1)
        
        # 今日出勤率
        today_attendance = Attendance.query.join(Employee).filter(
            Employee.admin_id == admin.id,
            Attendance.date == today,
            Attendance.status == '上班'
        ).count()
        today_attendance_rate = round((today_attendance / total_employees * 100), 1) if total_employees > 0 else 0
        
        # 未还借款总额
        total_unpaid_loans = db.session.query(db.func.sum(Loan.amount)).join(Employee).filter(
            Employee.admin_id == admin.id,
            Loan.status == '未还'
        ).scalar() or 0
        
        # 本月请假人数
        monthly_leaves = db.session.query(db.func.count(db.distinct(Attendance.employee_id))).join(Employee).filter(
            Employee.admin_id == admin.id,
            Attendance.date >= first_day,
            Attendance.date <= today,
            Attendance.status == '请假'
        ).scalar() or 0
        
        # 获取所有职位列表
        positions = db.session.query(db.distinct(Employee.position)).filter(
            Employee.admin_id == admin.id,
            Employee.position != None,
            Employee.position != ''
        ).all()
        positions = [pos[0] for pos in positions]
        
        return render_template('index.html',
            employees=employees,
            total_employees=total_employees,
            today_attendance_rate=today_attendance_rate,
            total_unpaid_loans=total_unpaid_loans,
            monthly_leave_count=monthly_leaves,
            positions=positions,
            is_super_admin=False
        )

@app.route('/add_employee', methods=['GET', 'POST'])
@login_required
def add_employee():
    if request.method == 'POST':
        name = request.form['name']
        position = request.form['position']
        
        # 获取当前登录的管理员
        admin = Admin.query.filter_by(username=session.get('username')).first()
        if not admin:
            flash('请先登录')
            return redirect(url_for('login'))
        
        employee = Employee(
            name=name,
            position=position,
            admin_id=admin.id  # 设置 admin_id
        )
        db.session.add(employee)
        
        # 记录添加员工日志
        log = AdminActivityLog(
            admin_id=admin.id,
            action_type='添加员工',
            details=f'添加新员工：{name}，职位：{position}'
        )
        db.session.add(log)
        db.session.commit()
        
        flash('员工添加成功！')
        return redirect(url_for('index'))
    
    return render_template('add_employee.html')

@app.route('/record_attendance/<int:employee_id>', methods=['POST'])
@login_required
def record_attendance(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    status = request.form['status']
    note = request.form.get('note', '')
    hours = float(request.form.get('hours', 8.0))  # 默认8小时
    
    # 创建考勤记录
    attendance = Attendance(
        employee_id=employee_id,
        date=date.today(),
        status=status,
        hours=hours,
        note=note
    )
    
    db.session.add(attendance)
    db.session.commit()
    
    flash(f'{employee.name}的{status}记录已添加！')
    
    # 获取当前月份的开始日期和结束日期
    today = date.today()
    start_date = date(today.year, today.month, 1)
    end_date = today
    
    # 获取考勤记录
    attendance_records = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.date.between(start_date, end_date)
    ).order_by(Attendance.date.desc()).all()
    
    # 计算统计数据
    total_days = (end_date - start_date).days + 1
    work_days = sum(1 for record in attendance_records if record.status == '上班')
    leave_days = sum(1 for record in attendance_records if record.status == '请假')
    attendance_rate = round((work_days / total_days * 100), 1) if total_days > 0 else 0
    
    return render_template('attendance.html',
                         employee=employee,
                         attendance_records=attendance_records,
                         start_date=start_date,
                         end_date=end_date,
                         total_days=total_days,
                         work_days=work_days,
                         leave_days=leave_days,
                         attendance_rate=attendance_rate)

@app.route('/loan/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def loan(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    admin = Admin.query.filter_by(username=session.get('username')).first()
    
    if request.method == 'POST':
        amount = float(request.form['amount'])
        purpose = request.form.get('purpose', '')
        
        loan = Loan(
            employee_id=employee_id,
            loan_date=date.today(),
            amount=amount,
            purpose=purpose,
            status='未还'
        )
        db.session.add(loan)
        
        # 记录借款日志
        log = AdminActivityLog(
            admin_id=admin.id,
            action_type='员工借款',
            details=f'员工 {employee.name} 借款：¥{amount}，用途：{purpose}'
        )
        db.session.add(log)
        db.session.commit()
        
        flash(f'已记录{employee.name}的借款：¥{amount}')
        return redirect(url_for('loan', employee_id=employee_id))
    
    return render_template('loan.html', employee=employee)

@app.route('/repay_loan/<int:loan_id>', methods=['POST'])
@login_required
def repay_loan(loan_id):
    loan = Loan.query.get_or_404(loan_id)
    admin = Admin.query.filter_by(username=session.get('username')).first()
    
    loan.status = '已还'
    loan.repayment_date = date.today()
    
    # 记录还款日志
    log = AdminActivityLog(
        admin_id=admin.id,
        action_type='员工还款',
        details=f'员工 {loan.employee.name} 还款：¥{loan.amount}'
    )
    db.session.add(log)
    db.session.commit()
    
    flash(f'已确认还款：¥{loan.amount}')
    return redirect(url_for('loan', employee_id=loan.employee_id))

@app.route('/export_excel', methods=['GET', 'POST'])
@login_required
def export_excel():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if start_date and end_date:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    else:
        today = date.today()
        start_date = date(today.year, today.month, 1)
        end_date = today
    
    employees = Employee.query.all()
    data = []
    
    for emp in employees:
        # 获取考勤记录
        attendance_records = Attendance.query.filter(
            Attendance.employee_id == emp.id,
            Attendance.date.between(start_date, end_date)
        ).all()
        
        # 计算考勤统计
        total_days = len(attendance_records)
        work_days = sum(1 for a in attendance_records if a.status == '上班')
        leave_days = sum(1 for a in attendance_records if a.status == '请假')
        
        # 获取借款记录
        loans = Loan.query.filter(
            Loan.employee_id == emp.id,
            Loan.loan_date.between(start_date, end_date)
        ).all()
        
        # 计算借款统计
        total_loans = sum(loan.amount for loan in loans)
        unpaid_loans = emp.get_total_unpaid_loans()
        repaid_loans = sum(loan.amount for loan in loans if loan.status == '已还')
        
        data.append({
            '工号': emp.id,
            '姓名': emp.name,
            '职位': emp.position,
            '考勤天数': total_days,
            '出勤天数': work_days,
            '请假天数': leave_days,
            '出勤率': f'{(work_days/total_days*100):.1f}%' if total_days > 0 else '0%',
            '本期借款': total_loans,
            '已还金额': repaid_loans,
            '未还金额': unpaid_loans
        })
    
    df = pd.DataFrame(data)
    output = BytesIO()
    
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='员工信息', index=False)
        workbook = writer.book
        worksheet = writer.sheets['员工信息']
        
        # 设置表格样式
        header_format = workbook.add_format({
            'bold': True,
            'align': 'center',
            'valign': 'vcenter',
            'fg_color': '#D7E4BC',
            'border': 1
        })
        
        number_format = workbook.add_format({
            'num_format': '#,##0.00',
            'align': 'right'
        })
        
        percent_format = workbook.add_format({
            'num_format': '0.0%',
            'align': 'center'
        })
        
        # 应用格式
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)
            if value in ['考勤天数', '出勤天数', '请假天数', '本期借款', '已还金额', '未还金额']:
                worksheet.set_column(col_num, col_num, 12, number_format)
            elif value == '出勤率':
                worksheet.set_column(col_num, col_num, 10, percent_format)
            else:
                worksheet.set_column(col_num, col_num, 12)
        
        # 添加考勤详情表
        attendance_data = []
        for emp in employees:
            records = Attendance.query.filter(
                Attendance.employee_id == emp.id,
                Attendance.date.between(start_date, end_date)
            ).order_by(Attendance.date).all()
            
            for record in records:
                attendance_data.append({
                    '日期': record.date,
                    '工号': emp.id,
                    '姓名': emp.name,
                    '考勤状态': record.status,
                    '工作时长': record.hours,
                    '备注': record.note or ''
                })
        
        if attendance_data:
            df_attendance = pd.DataFrame(attendance_data)
            df_attendance.to_excel(writer, sheet_name='考勤详情', index=False)
            worksheet_attendance = writer.sheets['考勤详情']
            
            for col_num, value in enumerate(df_attendance.columns.values):
                worksheet_attendance.write(0, col_num, value, header_format)
                worksheet_attendance.set_column(col_num, col_num, 15)
        
        # 添加借款详情表
        loan_data = []
        for emp in employees:
            loans = Loan.query.filter(
                Loan.employee_id == emp.id,
                Loan.loan_date.between(start_date, end_date)
            ).order_by(Loan.loan_date).all()
            
            for loan in loans:
                loan_data.append({
                    '借款日期': loan.loan_date,
                    '工号': emp.id,
                    '姓名': emp.name,
                    '借款金额': loan.amount,
                    '借款用途': loan.purpose or '',
                    '状态': loan.status,
                    '还款日期': loan.repayment_date or ''
                })
        
        if loan_data:
            df_loan = pd.DataFrame(loan_data)
            df_loan.to_excel(writer, sheet_name='借款详情', index=False)
            worksheet_loan = writer.sheets['借款详情']
            
            for col_num, value in enumerate(df_loan.columns.values):
                worksheet_loan.write(0, col_num, value, header_format)
                if value == '借款金额':
                    worksheet_loan.set_column(col_num, col_num, 12, number_format)
                else:
                    worksheet_loan.set_column(col_num, col_num, 15)

    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'员工数据_{start_date.strftime("%Y%m%d")}_{end_date.strftime("%Y%m%d")}.xlsx'
    )

@app.route('/delete_employee/<int:employee_id>', methods=['POST'])
@login_required
def delete_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    admin = Admin.query.filter_by(username=session.get('username')).first()
    
    # 删除相关的考勤记录
    Attendance.query.filter_by(employee_id=employee_id).delete()
    
    # 删除相关的借款记录
    Loan.query.filter_by(employee_id=employee_id).delete()
    
    # 记录删除员工日志
    log = AdminActivityLog(
        admin_id=admin.id,
        action_type='删除员工',
        details=f'删除员工：{employee.name}，职位：{employee.position}'
    )
    db.session.add(log)
    
    # 删除员工
    db.session.delete(employee)
    db.session.commit()
    
    flash(f'员工 {employee.name} 已删除')
    return redirect(url_for('index'))

# 登录路由
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form['password']
        
        # 检查是否是超级管理员登录
        if username == 'superadmin' and password == 'superadmin123':
            session.clear()
            session['username'] = username
            session['is_super_admin'] = True
            session['logged_in'] = True
            flash('超级管理员登录成功')
            return redirect(url_for('index'))
        
        # 检查普通管理员登录
        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.check_password(password):
            if not admin.is_active:
                flash('账户已被禁用')
                return redirect(url_for('login'))
            
            # 更新最后登录时间
            admin.last_login = datetime.utcnow()
            
            # 记录登录日志
            log = AdminActivityLog(
                admin_id=admin.id,
                action_type='登录',
                details='管理员登录系统'
            )
            db.session.add(log)
            db.session.commit()
            
            session.clear()
            session['username'] = username
            session['logged_in'] = True
            flash('登录成功')
            return redirect(url_for('index'))
        else:
            flash('用户名或密码错误')
    
    return render_template('login.html')

# 注销路由
@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('已注销')
    return redirect(url_for('login'))

@app.route('/export_employee/<int:employee_id>')
@login_required
def export_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if start_date and end_date:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    else:
        today = date.today()
        start_date = date(today.year, today.month, 1)
        end_date = today
    
    output = BytesIO()
    
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # 设置表格样式
        header_format = workbook.add_format({
            'bold': True,
            'align': 'center',
            'valign': 'vcenter',
            'fg_color': '#D7E4BC',
            'border': 1
        })
        
        number_format = workbook.add_format({
            'num_format': '#,##0.00',
            'align': 'right'
        })
        
        # 导出考勤记录
        attendance_records = Attendance.query.filter(
            Attendance.employee_id == employee_id,
            Attendance.date.between(start_date, end_date)
        ).order_by(Attendance.date).all()
        
        attendance_data = []
        for record in attendance_records:
            attendance_data.append({
                '日期': record.date,
                '考勤状态': record.status,
                '工作时长': record.hours,
                '备注': record.note or ''
            })
        
        if attendance_data:
            df_attendance = pd.DataFrame(attendance_data)
            df_attendance.to_excel(writer, sheet_name='考勤记录', index=False)
            worksheet = writer.sheets['考勤记录']
            
            for col_num, value in enumerate(df_attendance.columns.values):
                worksheet.write(0, col_num, value, header_format)
                if value == '工作时长':
                    worksheet.set_column(col_num, col_num, 12, number_format)
                else:
                    worksheet.set_column(col_num, col_num, 15)
        
        # 导出借款记录
        loans = Loan.query.filter(
            Loan.employee_id == employee_id,
            Loan.loan_date.between(start_date, end_date)
        ).order_by(Loan.loan_date).all()
        
        loan_data = []
        for loan in loans:
            loan_data.append({
                '借款日期': loan.loan_date,
                '借款金额': loan.amount,
                '借款用途': loan.purpose or '',
                '状态': loan.status,
                '还款日期': loan.repayment_date or ''
            })
        
        if loan_data:
            df_loan = pd.DataFrame(loan_data)
            df_loan.to_excel(writer, sheet_name='借款记录', index=False)
            worksheet = writer.sheets['借款记录']
            
            for col_num, value in enumerate(df_loan.columns.values):
                worksheet.write(0, col_num, value, header_format)
                if value == '借款金额':
                    worksheet.set_column(col_num, col_num, 12, number_format)
                else:
                    worksheet.set_column(col_num, col_num, 15)
        
        # 导出轮休记录
        rotations = Rotation.query.filter(
            Rotation.employee_id == employee_id,
            Rotation.start_date.between(start_date, end_date)
        ).order_by(Rotation.start_date).all()
        
        rotation_data = []
        for rotation in rotations:
            rotation_data.append({
                '日期': rotation.start_date,
                '状态': '上班' if rotation.is_working_week else '休息',
                '备注': rotation.note or ''
            })
        
        if rotation_data:
            df_rotation = pd.DataFrame(rotation_data)
            df_rotation.to_excel(writer, sheet_name='轮休记录', index=False)
            worksheet = writer.sheets['轮休记录']
            
            for col_num, value in enumerate(df_rotation.columns.values):
                worksheet.write(0, col_num, value, header_format)
                worksheet.set_column(col_num, col_num, 15)
        
        # 添加统计信息
        total_days = len(attendance_records)
        work_days = sum(1 for a in attendance_records if a.status == '上班')
        leave_days = sum(1 for a in attendance_records if a.status == '请假')
        total_loans = sum(loan.amount for loan in loans)
        repaid_loans = sum(loan.amount for loan in loans if loan.status == '已还')
        
        summary_data = [{
            '姓名': employee.name,
            '工号': employee.id,
            '职位': employee.position,
            '考勤天数': total_days,
            '出勤天数': work_days,
            '请假天数': leave_days,
            '出勤率': f'{(work_days/total_days*100):.1f}%' if total_days > 0 else '0%',
            '本期借款': total_loans,
            '已还金额': repaid_loans,
            '未还金额': employee.get_total_unpaid_loans()
        }]
        
        df_summary = pd.DataFrame(summary_data)
        df_summary.to_excel(writer, sheet_name='员工信息', index=False)
        worksheet = writer.sheets['员工信息']
        
        for col_num, value in enumerate(df_summary.columns.values):
            worksheet.write(0, col_num, value, header_format)
            if value in ['考勤天数', '出勤天数', '请假天数', '本期借款', '已还金额', '未还金额']:
                worksheet.set_column(col_num, col_num, 12, number_format)
            else:
                worksheet.set_column(col_num, col_num, 15)
    
    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'{employee.name}_数据_{start_date.strftime("%Y%m%d")}_{end_date.strftime("%Y%m%d")}.xlsx'
    )

@app.route('/batch_attendance', methods=['GET', 'POST'])
@login_required
def batch_attendance():
    # 获取当前登录的管理员
    username = session.get('username')
    is_super_admin = session.get('is_super_admin', False)
    
    if is_super_admin:
        # 超级管理员可以看到所有员工
        employees = Employee.query.all()
    else:
        # 普通管理员只能看到自己的员工
        admin = Admin.query.filter_by(username=username).first()
        if not admin:
            flash('账户异常，请重新登录')
            return redirect(url_for('logout'))
        employees = Employee.query.filter_by(admin_id=admin.id).all()
    
    today = date.today()
    
    if request.method == 'POST':
        start_date_str = request.form.get('start_date', today.strftime('%Y-%m-%d'))
        end_date_str = request.form.get('end_date', today.strftime('%Y-%m-%d'))
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        
        # 获取日期范围内的所有日期
        date_list = []
        current_date = start_date
        while current_date <= end_date:
            # 检查是否是假期
            holiday = Holiday.query.filter_by(date=current_date).first()
            if not holiday:  # 如果不是假期，则添加到日期列表
                date_list.append(current_date)
            current_date += timedelta(days=1)
        
        # 处理每个员工的考勤
        selected_employees = request.form.getlist('selected_employees')
        for employee_id in selected_employees:
            # 验证员工属于当前管理员
            employee = Employee.query.get(employee_id)
            if not is_super_admin and employee.admin_id != admin.id:
                continue  # 跳过不属于当前管理员的员工
                
            status = request.form.get(f'status_{employee_id}')
            hours = float(request.form.get(f'hours_{employee_id}', 8.0))
            note = request.form.get(f'note_{employee_id}', '')
            
            if status:  # 只处理选中的员工
                # 为日期范围内的每一天创建考勤记录
                for attendance_date in date_list:
                    existing_record = Attendance.query.filter_by(
                        employee_id=employee_id,
                        date=attendance_date
                    ).first()
                    
                    if existing_record:
                        existing_record.status = status
                        existing_record.hours = hours
                        existing_record.note = note
                    else:
                        attendance = Attendance(
                            employee_id=employee_id,
                            date=attendance_date,
                            status=status,
                            hours=hours,
                            note=note
                        )
                        db.session.add(attendance)
        
        db.session.commit()
        flash(f'已更新{start_date}到{end_date}的考勤记录')
        return redirect(url_for('index'))
    
    return render_template('batch_attendance.html', employees=employees, today=today)

@app.route('/edit_employee/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def edit_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    
    if request.method == 'POST':
        employee.name = request.form['name']
        employee.position = request.form['position']
        
        db.session.commit()
        flash('员工资料已更新！')
        return redirect(url_for('index'))
    
    return render_template('edit_employee.html', employee=employee)

@app.route('/attendance/<int:employee_id>')
@login_required
def attendance(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    today = date.today()
    first_day = date(today.year, today.month, 1)
    
    # 获取查询参数中的日期范围
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if start_date and end_date:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    else:
        start_date = first_day
        end_date = today
    
    # 获取考勤记录
    attendance_records = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.date.between(start_date, end_date)
    ).order_by(Attendance.date.desc()).all()
    
    # 计算统计数据
    total_days = (end_date - start_date).days + 1
    work_days = sum(1 for record in attendance_records if record.status == '上班')
    leave_days = sum(1 for record in attendance_records if record.status == '请假')
    attendance_rate = round((work_days / total_days * 100), 1) if total_days > 0 else 0
    
    return render_template('attendance.html',
                         employee=employee,
                         attendance_records=attendance_records,
                         start_date=start_date,
                         end_date=end_date,
                         total_days=total_days,
                         work_days=work_days,
                         leave_days=leave_days,
                         attendance_rate=attendance_rate)

@app.route('/salary_settlement/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def salary_settlement(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    admin = Admin.query.filter_by(username=session.get('username')).first()
    today = date.today()
    
    if request.method == 'POST':
        start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
        end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d').date()
        amount = float(request.form['amount'])
        note = request.form.get('note', '')
        
        settlement = SalarySettlement(
            employee_id=employee_id,
            start_date=start_date,
            end_date=end_date,
            amount=amount,
            settlement_date=today,
            note=note
        )
        db.session.add(settlement)
        
        # 记录工资结算日志
        log = AdminActivityLog(
            admin_id=admin.id,
            action_type='工资结算',
            details=f'为员工 {employee.name} 结算工资：¥{amount}，时间段：{start_date} 至 {end_date}'
        )
        db.session.add(log)
        db.session.commit()
        
        flash(f'已记录{employee.name}从{start_date}到{end_date}的工资结算：¥{amount}')
        return redirect(url_for('index'))
    
    settlements = SalarySettlement.query.filter_by(
        employee_id=employee_id
    ).order_by(SalarySettlement.end_date.desc()).all()
    
    return render_template('salary_settlement.html', 
                         employee=employee, 
                         settlements=settlements,
                         today=today)

@app.route('/manage_holidays', methods=['GET', 'POST'])
@login_required
def manage_holidays():
    if request.method == 'POST':
        date_str = request.form['date']
        holiday_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        name = request.form['name']
        note = request.form.get('note', '')
        
        existing_holiday = Holiday.query.filter_by(date=holiday_date).first()
        if existing_holiday:
            existing_holiday.name = name
            existing_holiday.note = note
        else:
            holiday = Holiday(
                date=holiday_date,
                name=name,
                note=note
            )
            db.session.add(holiday)
        
        db.session.commit()
        flash(f'已设置{holiday_date}为{name}')
        return redirect(url_for('manage_holidays'))
    
    holidays = Holiday.query.order_by(Holiday.date.desc()).all()
    return render_template('holidays.html', holidays=holidays)

@app.route('/delete_holiday/<int:holiday_id>', methods=['POST'])
@login_required
def delete_holiday(holiday_id):
    holiday = Holiday.query.get_or_404(holiday_id)
    db.session.delete(holiday)
    db.session.commit()
    flash(f'已删除{holiday.date}的假期设置')
    return redirect(url_for('manage_holidays'))

@app.route('/dashboard')
@login_required
def dashboard():
    # 获取系统信息
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot_time
    uptime_str = f"{uptime.days}天 {uptime.seconds//3600}小时"

    # 获取进程信息
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
        try:
            pinfo = proc.info
            pinfo['cpu_percent'] = proc.cpu_percent(interval=0.1)
            pinfo['memory_percent'] = proc.memory_percent()
            processes.append(pinfo)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # 按CPU使用率排序，只显示前10个进程
    processes.sort(key=lambda x: x['cpu_percent'], reverse=True)
    top_processes = processes[:10]

    # 获取性能数据（最近24小时）
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=24)
    
    # 生成时间戳列表（每小时一个点）
    timestamps = []
    cpu_data = []
    memory_data = []
    
    current_time = start_time
    while current_time <= end_time:
        timestamps.append(current_time.strftime('%H:00'))
        cpu_data.append(psutil.cpu_percent())
        memory_data.append(psutil.virtual_memory().percent)
        current_time += timedelta(hours=1)

    # 获取系统日志
    try:
        with open('system.log', 'r') as f:
            logs = f.readlines()[-50:]  # 只显示最后50行
    except FileNotFoundError:
        logs = ['系统日志文件不存在']
    
    return render_template('dashboard.html',
        cpu_percent=cpu_percent,
        memory=memory,
        disk=disk,
        uptime=uptime_str,
        processes=top_processes,
        timestamps=timestamps,
        cpu_data=cpu_data,
        memory_data=memory_data,
        logs=logs
    )

@app.route('/rotation/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def manage_rotation(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    today = date.today()
    
    if request.method == 'POST':
        start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
        note = request.form.get('note', '')
        
        # 确保开始日期是星期天
        while start_date.weekday() != 6:  # 6表示星期天
            start_date += timedelta(days=1)
        
        # 取这一年的最后一天
        year_end = date(start_date.year, 12, 31)
        
        # 删除该员工已存在的轮休记录
        Rotation.query.filter_by(employee_id=employee_id).delete()
        
        # 创建轮休记录
        current_date = start_date
        is_working_week = True  # 第一周是否上班
        
        while current_date <= year_end:
            if current_date.weekday() == 6:  # 只处理星期天
                rotation = Rotation(
                    employee_id=employee_id,
                    start_date=current_date,
                    is_working_week=is_working_week,
                    note=note
                )
                db.session.add(rotation)
                
                # 自动创建考勤记录
                existing_record = Attendance.query.filter_by(
                    employee_id=employee_id,
                    date=current_date
                ).first()
                
                if existing_record:
                    db.session.delete(existing_record)
                
                if is_working_week:  # 如果是工作周的星期天
                    attendance = Attendance(
                        employee_id=employee_id,
                        date=current_date,
                        status='上班',
                        hours=8.0,
                        note='轮休安排-工作日'
                    )
                    db.session.add(attendance)
                
                is_working_week = not is_working_week  # 切换状态
            
            current_date += timedelta(days=1)
        
        db.session.commit()
        flash(f'已设置{employee.name}的轮休安排')
        return redirect(url_for('index'))
    
    rotations = Rotation.query.filter_by(
        employee_id=employee_id
    ).order_by(Rotation.start_date).all()
    
    return render_template('rotation.html', 
                         employee=employee, 
                         rotations=rotations,
                         today=today)

# 修改密码路由
@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form['old_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        
        if old_password != app.config['ADMIN_PASSWORD']:
            flash('原密码错误')
            return redirect(url_for('change_password'))
            
        if new_password != confirm_password:
            flash('两次输入的新密码不一致')
            return redirect(url_for('change_password'))
            
        # 更新密码
        app.config['ADMIN_PASSWORD'] = new_password
        flash('密码修改成功')
        return redirect(url_for('index'))
    
    return render_template('change_password.html')

# 重置密码路由
@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        security_code = request.form['security_code']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        
        # 这里使用一个固定的安全码，实际应用中应该用更安全的方式
        if security_code != '888888':
            flash('安全码错误')
            return redirect(url_for('reset_password'))
            
        if new_password != confirm_password:
            flash('两次输入的新密码不一致')
            return redirect(url_for('reset_password'))
            
        # 更新密码
        app.config['ADMIN_PASSWORD'] = new_password
        flash('密码重置成功，请使用新密码登录')
        return redirect(url_for('login'))
    
    return render_template('reset_password.html')

@app.route('/attendance_calendar')
@login_required
def attendance_calendar():
    employees = Employee.query.all()
    return render_template('attendance_calendar.html', employees=employees)

@app.route('/get_attendance_data')
@login_required
def get_attendance_data():
    try:
        start_date = request.args.get('start')
        end_date = request.args.get('end')
        employee_id = request.args.get('employee_id')
        
        if not start_date or not end_date:
            return jsonify([])
            
        start = datetime.strptime(start_date[:10], '%Y-%m-%d').date()
        end = datetime.strptime(end_date[:10], '%Y-%m-%d').date()
        
        # 获取考勤记录
        query = Attendance.query.filter(
            Attendance.date.between(start, end)
        )
        
        if employee_id:
            query = query.filter(Attendance.employee_id == employee_id)
            
        records = query.all()
        
        events = []
        for record in records:
            color = '#28a745' if record.status == '上班' else '#ffc107'
            events.append({
                'title': f'{record.employee.name}: {record.status}',
                'start': record.date.isoformat(),
                'color': color,
                'extendedProps': {
                    'employee_name': record.employee.name,
                    'status': record.status,
                    'hours': record.hours,
                    'note': record.note or ''
                }
            })
        
        # 获取假期记录
        holidays = Holiday.query.filter(
            Holiday.date.between(start, end)
        ).all()
        
        for holiday in holidays:
            events.append({
                'title': f'假期-{holiday.name}',
                'start': holiday.date.isoformat(),
                'color': '#dc3545',
                'extendedProps': {
                    'type': '假期',
                    'name': holiday.name,
                    'note': holiday.note or ''
                }
            })
        
        return jsonify(events)
    except Exception as e:
        print(f"Error in get_attendance_data: {str(e)}")
        return jsonify({'error': str(e)}), 500

# 删除管理员（仅限超级管理员）
@app.route('/admin/delete/<int:admin_id>', methods=['POST'])
@super_admin_required
def delete_admin(admin_id):
    admin = Admin.query.get_or_404(admin_id)
    
    # 检查是否试图删除超级管理员
    if admin.username == app.config['SUPER_ADMIN_USERNAME']:
        flash('不能删除超级管理员账户')
        return redirect(url_for('admin_dashboard'))
    
    db.session.delete(admin)
    db.session.commit()
    flash('管理员已删除')
    return redirect(url_for('admin_dashboard'))

# 启用/禁用管理员账户（仅限超级管理员）
@app.route('/admin/toggle_admin_status/<int:admin_id>', methods=['POST'])
@super_admin_required
def toggle_admin_status(admin_id):
    admin = Admin.query.get_or_404(admin_id)
    
    # 检查是否试图修改超级管理员
    if admin.username == app.config['SUPER_ADMIN_USERNAME']:
        flash('不能修改超级管理员状态')
        return redirect(url_for('admin_dashboard'))
    
    # 切换状态
    admin.is_active = not admin.is_active
    db.session.commit()
    
    status = '启用' if admin.is_active else '禁用'
    flash(f'已{status}管理员账户：{admin.username}')
    return redirect(url_for('admin_dashboard'))

# 超级管理员仪表板
@app.route('/admin/dashboard')
@super_admin_required
def admin_dashboard():
    # 获取所有管理员账户（除了超级管理员）
    admins = Admin.query.filter(Admin.username != 'superadmin').order_by(Admin.created_at.desc()).all()
    
    # 获取所有员工信息
    employees = Employee.query.all()
    
    # 统计数据
    total_employees = len(employees)
    total_unpaid_loans = sum(emp.get_total_unpaid_loans() for emp in employees)
    
    # 获取今日考勤数据
    today = date.today()
    today_attendance = Attendance.query.filter_by(date=today, status='上班').count()
    attendance_rate = round((today_attendance / total_employees * 100), 1) if total_employees > 0 else 0
    
    return render_template('admin_dashboard.html',
                         admins=admins,
                         total_employees=total_employees,
                         total_unpaid_loans=total_unpaid_loans,
                         attendance_rate=attendance_rate)

# 添加管理员（注册新店铺）
@app.route('/admin/add', methods=['GET', 'POST'])
def add_admin():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form.get('confirm_password')
        email = request.form.get('email')
        store_name = request.form['store_name']
        
        # 检查密码确认
        if password != confirm_password:
            flash('两次输入的密码不一致')
            return redirect(url_for('login'))
        
        # 检查用户名是否已存在
        if Admin.query.filter_by(username=username).first():
            flash('用户名已存在')
            return redirect(url_for('login'))
        
        # 确保不能创建超级管理员
        if username == 'superadmin':
            flash('不能创建超级管理员账户')
            return redirect(url_for('login'))
            
        admin = Admin(
            username=username,
            email=email,
            store_name=store_name,
            is_active=True,
            created_at=datetime.utcnow(),
            last_login=None  # 初始化最后登录时间为空
        )
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        
        flash('店铺注册成功，请登录')
        return redirect(url_for('login'))
        
    return redirect(url_for('login'))

# 系统监控路由
@app.route('/system_monitor')
@super_admin_required
def system_monitor():
    try:
        # 获取CPU使用率
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # 获取内存信息
        memory = psutil.virtual_memory()
        
        # 获取磁盘信息
        disk = psutil.disk_usage('/')
        
        # 获取系统启动时间
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        uptime_str = f"{uptime.days}天 {uptime.seconds//3600}小时"
        
        # 获取进程信息
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
            try:
                pinfo = proc.info
                pinfo['cpu_percent'] = proc.cpu_percent(interval=0.1)
                pinfo['memory_percent'] = proc.memory_percent()
                processes.append(pinfo)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        
        # 按CPU使用率排序，只显示前10个进程
        processes.sort(key=lambda x: x['cpu_percent'], reverse=True)
        top_processes = processes[:10]
        
        # 获取历史性能数据（最近1小时，每分钟一个点）
        timestamps = []
        cpu_data = []
        memory_data = []
        
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)
        
        # 生成60个数据点（每分钟一个）
        for i in range(60):
            point_time = start_time + timedelta(minutes=i)
            timestamps.append(point_time.strftime('%H:%M'))
            cpu_data.append(psutil.cpu_percent())
            memory_data.append(psutil.virtual_memory().percent)
        
        return render_template('admin/system_monitor.html',
            cpu_percent=cpu_percent,
            memory=memory,
            disk=disk,
            uptime=uptime_str,
            processes=top_processes,
            timestamps=timestamps,
            cpu_data=cpu_data,
            memory_data=memory_data
        )
        
    except Exception as e:
        app.logger.error(f"Error in system_monitor: {str(e)}")
        flash('获取系统信息时发生错误')
        return redirect(url_for('admin_dashboard'))

# 系统日志路由
@app.route('/system_logs')
@super_admin_required
def system_logs():
    try:
        with open('system.log', 'r') as f:
            logs = f.readlines()
        return render_template('admin/system_logs.html', logs=logs)
    except FileNotFoundError:
        flash('系统日志文件不存在')
        return redirect(url_for('admin_dashboard'))

# 警报配置路由
@app.route('/alert_config', methods=['GET', 'POST'])
@super_admin_required
def alert_config():
    config_file = 'alert_config.json'
    
    if request.method == 'POST':
        config = {
            'cpu_threshold': float(request.form['cpu_threshold']),
            'memory_threshold': float(request.form['memory_threshold']),
            'disk_threshold': float(request.form['disk_threshold']),
            'email_notifications': request.form.get('email_notifications') == 'on'
        }
        
        with open(config_file, 'w') as f:
            json.dump(config, f)
            
        flash('警报配置已更新')
        return redirect(url_for('alert_config'))
    
    # 读取现有配置
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        config = {
            'cpu_threshold': 80,
            'memory_threshold': 80,
            'disk_threshold': 80,
            'email_notifications': False
        }
    
    return render_template('admin/alert_config.html', config=config)

# 性能报告路由
@app.route('/performance_report')
@super_admin_required
def performance_report():
    # 获取过去24小时的性能数据
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=24)
    
    # 模拟性能数据（实际应用中应该从数据库获取）
    performance_data = {
        'timestamps': [],
        'cpu_usage': [],
        'memory_usage': [],
        'disk_usage': []
    }
    
    current_time = start_time
    while current_time <= end_time:
        performance_data['timestamps'].append(current_time.strftime('%Y-%m-%d %H:%M'))
        performance_data['cpu_usage'].append(psutil.cpu_percent())
        performance_data['memory_usage'].append(psutil.virtual_memory().percent)
        performance_data['disk_usage'].append(psutil.disk_usage('/').percent)
        current_time += timedelta(hours=1)
    
    return render_template('admin/performance_report.html', 
                         performance_data=performance_data)

@app.route('/admin/account/<int:admin_id>')
@super_admin_required
def admin_account_details(admin_id):
    admin = Admin.query.get_or_404(admin_id)
    
    # 获取该管理员的所有员工
    employees = Employee.query.filter_by(admin_id=admin_id).all()
    
    # 计算每个员工的考勤率和未还借款
    for employee in employees:
        # 计算本月考勤率
        today = date.today()
        first_day = date(today.year, today.month, 1)
        attendance_records = Attendance.query.filter(
            Attendance.employee_id == employee.id,
            Attendance.date.between(first_day, today)
        ).all()
        
        total_days = (today - first_day).days + 1
        work_days = sum(1 for record in attendance_records if record.status == '上班')
        employee.attendance_rate = round((work_days / total_days * 100), 1) if total_days > 0 else 0
        
        # 计算未还借款
        employee.unpaid_loans = employee.get_total_unpaid_loans()
    
    # 获取职位分布数据
    positions = {}
    for employee in employees:
        position = employee.position or '未设置'
        positions[position] = positions.get(position, 0) + 1
    
    position_labels = list(positions.keys())
    position_counts = list(positions.values())
    
    # 获取过去30天的考勤率趋势
    end_date = date.today()
    start_date = end_date - timedelta(days=29)
    attendance_dates = []
    attendance_rates = []
    
    current_date = start_date
    while current_date <= end_date:
        attendance_records = Attendance.query.join(Employee).filter(
            Employee.admin_id == admin_id,
            Attendance.date == current_date
        ).all()
        
        total_employees = len(employees)
        work_employees = sum(1 for record in attendance_records if record.status == '上班')
        rate = round((work_employees / total_employees * 100), 1) if total_employees > 0 else 0
        
        attendance_dates.append(current_date.strftime('%m-%d'))
        attendance_rates.append(rate)
        current_date += timedelta(days=1)
    
    # 获取最近的操作日志
    activity_logs = AdminActivityLog.query.filter_by(
        admin_id=admin_id
    ).order_by(AdminActivityLog.timestamp.desc()).limit(50).all()
    
    return render_template('admin/account_details.html',
        admin=admin,
        employees=employees,
        position_labels=position_labels,
        position_counts=position_counts,
        attendance_dates=attendance_dates,
        attendance_rates=attendance_rates,
        activity_logs=activity_logs
    )

# 编辑管理员账户路由
@app.route('/edit_admin/<int:admin_id>', methods=['GET', 'POST'])
@super_admin_required
def edit_admin(admin_id):
    admin = Admin.query.get_or_404(admin_id)
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        store_name = request.form.get('store_name')
        new_password = request.form.get('new_password')
        
        # 检查用户名是否已存在（排除当前账户）
        existing_admin = Admin.query.filter(
            Admin.username == username,
            Admin.id != admin_id
        ).first()
        if existing_admin:
            flash('用户名已存在')
            return redirect(url_for('edit_admin', admin_id=admin_id))
        
        # 更新管理员信息
        admin.username = username
        admin.email = email
        admin.store_name = store_name
        
        # 如果提供了新密码，则更新密码
        if new_password:
            admin.set_password(new_password)
        
        # 记录编辑日志
        log = AdminActivityLog(
            admin_id=admin_id,
            action_type='编辑账户',
            details=f'更新账户信息：{username}'
        )
        db.session.add(log)
        db.session.commit()
        
        flash('账户信息已更新')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/edit_admin.html', admin=admin)

if __name__ == '__main__':
    with app.app_context():
        # 只在数据库不存在时创建表
        db.create_all()
    app.run(debug=True)
print("Current directory:", os.getcwd())
print("Template directory:", app.template_folder) 

