# -*- coding: utf-8 -*-
"""
股票定投策略分析后端API
适配宝塔面板部署 | 支持Flask内置服务器/uWSGI运行
端口：8002 | 核心接口：/api/stock_data /api/analyze_strategy
"""
from flask import Flask, jsonify, request
from flask_cors import CORS
import baostock as bs
import datetime
import time
import numpy as np
import logging
from decimal import Decimal, ROUND_HALF_UP
# 拼音处理依赖
from pypinyin import lazy_pinyin, Style
# 系统模块（路径配置）
import os

# ====================== 1. 基础配置（适配宝塔路径） ======================
# 后端根目录（根据实际部署路径调整）
BASE_DIR = "/www/wwwroot/43.138.21.195/backend"
# 确保目录存在
os.makedirs(BASE_DIR, exist_ok=True)

# ====================== 2. 日志配置（兼容宝塔查看） ======================
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
# 赋予www用户权限（宝塔运行用户）
try:
    os.chmod(LOG_DIR, 0o755)
    if os.path.exists("/usr/bin/chown"):
        os.system(f"chown www:www {LOG_DIR}")
except Exception as e:
    print(f"日志目录权限设置警告：{e}")

# 日志格式配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y/%m/%d %H:%M:%S',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "stock_api.log"), encoding="utf-8"),
        logging.StreamHandler()  # 终端输出，方便调试
    ]
)
logger = logging.getLogger(__name__)

# ====================== 3. Flask应用初始化 ======================
app = Flask(__name__)
# 跨域配置：允许所有域名+携带凭证（适配前端请求）
CORS(app, resources=r"/*", supports_credentials=True)

# ====================== 4. 股票名称-代码映射表 ======================
STOCK_NAME_MAP = {
    "贵州茅台": "sh.600519",
    "宁德时代": "sz.300750",
    "腾讯控股": "hk.00700",
    "比亚迪": "sz.002594",
    "中国平安": "sh.601318",
    "招商银行": "sh.600036",
    "五粮液": "sz.000858",
    "隆基绿能": "sh.601012",
    "迈瑞医疗": "sz.300760",
    "恒瑞医药": "sh.600276",
    "中信证券": "sh.600030",
    "东方财富": "sz.300059",
    "立讯精密": "sz.002475",
    "泸州老窖": "sz.000568",
    "兆易创新": "sh.603986",
    "黑牡丹": "sh.600510",
}
# 反向映射：代码→名称
CODE_TO_STOCK_NAME = {v: k for k, v in STOCK_NAME_MAP.items()}

# ====================== 5. 工具函数 ======================
def to_valid_date(date_str):
    """转换为有效日期对象，兼容多种格式"""
    if not date_str:
        raise ValueError("日期不能为空")
    try:
        clean_str = date_str.replace("年", "-").replace("月", "-").replace("日", "").replace(".", "-").replace("/", "-")
        return datetime.datetime.strptime(clean_str, "%Y-%m-%d").date()
    except Exception as e:
        raise ValueError(f"日期格式错误：{date_str}，错误：{str(e)}")

def get_iso_week(date_obj):
    """获取ISO周数（年+周）"""
    week_num = date_obj.isocalendar()[1]
    return f"{date_obj.year}{week_num:02d}"

def round_decimal(value, decimal_places=2):
    """精准四舍五入"""
    return float(Decimal(str(value)).quantize(Decimal(f"0.{'0'*decimal_places}"), rounding=ROUND_HALF_UP))

def get_pinyin_first_letter(chinese_str):
    """提取中文字符串拼音首字母（大写）"""
    if not chinese_str or not isinstance(chinese_str, str):
        return ""
    try:
        pinyin_list = lazy_pinyin(chinese_str, style=Style.FIRST_LETTER)
        return ''.join([p.upper() for p in pinyin_list])
    except Exception as e:
        logger.error(f"提取拼音首字母失败：{e}")
        return ""

def generate_name_first_letter_map():
    """生成股票名称→拼音首字母映射表"""
    name_letter_map = {}
    for name, code in STOCK_NAME_MAP.items():
        first_letters = get_pinyin_first_letter(name)
        name_letter_map[name] = {
            "code": code,
            "first_letter": first_letters,
            "is_hk": code.startswith("hk.")
        }
    return name_letter_map
# 初始化首字母映射表
NAME_FIRST_LETTER_MAP = generate_name_first_letter_map()

def get_stock_name_from_baostock(code):
    """从Baostock实时查询股票名称（兜底用）"""
    if not code or not (code.startswith("sh.") or code.startswith("sz.")):
        return None
    try:
        lg = bs.login()
        if lg.error_code != '0':
            logger.warning(f"Baostock登录失败：{lg.error_msg}")
            bs.logout()
            return None
        
        rs = bs.query_stock_basic(code=code)
        if rs.error_code != '0':
            logger.warning(f"查询{code}基本信息失败：{rs.error_msg}")
            bs.logout()
            return None
        
        stock_name = None
        if rs.next():
            row_data = rs.get_row_data()
            if len(row_data) >= 2:
                stock_name = row_data[1]
        
        bs.logout()
        return stock_name if stock_name else None
    except Exception as e:
        logger.error(f"从Baostock获取{code}名称失败：{e}", exc_info=True)
        try:
            bs.logout()
        except:
            pass
        return None

def fuzzy_match_stock_by_name(input_name):
    """模糊匹配股票名称"""
    if not input_name:
        return []
    input_name = input_name.strip()
    matched_list = []
    for name, code in STOCK_NAME_MAP.items():
        if input_name in name:
            matched_list.append({
                "name": name,
                "code": code,
                "is_hk": code.startswith("hk.")
            })
    matched_list.sort(key=lambda x: len(x["name"]))
    return matched_list

def match_stock_by_first_letter(input_letter):
    """通过拼音首字母匹配股票"""
    if not input_letter:
        return []
    input_letter = input_letter.strip().upper()
    matched_list = []
    for name, info in NAME_FIRST_LETTER_MAP.items():
        if info["first_letter"].startswith(input_letter) or info["first_letter"] == input_letter:
            matched_list.append({
                "name": name,
                "code": info["code"],
                "first_letter": info["first_letter"],
                "is_hk": info["is_hk"]
            })
    matched_list.sort(key=lambda x: (x["first_letter"] != input_letter, len(x["first_letter"])))
    return matched_list

def match_stock_code(input_str):
    """通用匹配（代码/名称/首字母）"""
    if not input_str:
        return None, False
    input_str = input_str.strip()
    
    # 1. 完整代码匹配
    is_hk = input_str.startswith('hk.')
    if input_str.startswith(('sh.', 'sz.', 'hk.')):
        logger.info(f"匹配到完整代码：{input_str}（港股：{is_hk}）")
        return input_str, is_hk
    
    # 2. 纯数字补前缀
    if input_str.isdigit():
        if input_str.startswith(('60', '68')):
            code = f"sh.{input_str}"
            return code, False
        elif input_str.startswith(('00', '30')):
            code = f"sz.{input_str}"
            return code, False
        elif len(input_str) == 5:
            code = f"hk.{input_str}"
            return code, True
    
    # 3. 首字母匹配
    letter_matched = match_stock_by_first_letter(input_str)
    if letter_matched:
        logger.info(f"首字母匹配：{input_str} → {letter_matched[0]['name']}（{letter_matched[0]['code']}）")
        return letter_matched[0]["code"], letter_matched[0]["is_hk"]
    
    # 4. 模糊名称匹配
    name_matched = fuzzy_match_stock_by_name(input_str)
    if name_matched:
        logger.info(f"模糊名称匹配：{input_str} → {name_matched[0]['name']}（{name_matched[0]['code']}）")
        return name_matched[0]["code"], name_matched[0]["is_hk"]
    
    return None, False

# ====================== 6. 股票数据获取函数 ======================
def calculate_pe_quantile(pe_list):
    """计算PE分位点"""
    quantiles = [0.0] * len(pe_list)
    if len(pe_list) < 21:
        logger.warning("PE数据量不足21条，分位点计算结果为0")
        return quantiles
    
    for i in range(20, len(pe_list)):
        try:
            current_pe = pe_list[i]
            if current_pe <= 0:
                continue
            valid_history = [x for x in pe_list[:i+1] if x > 0]
            if len(valid_history) < 5:
                continue
            percentiles = np.percentile(valid_history, np.arange(0, 101))
            q = np.searchsorted(percentiles, current_pe) / 100.0
            quantiles[i] = round_decimal(min(q * 100, 100.0), 2)
        except Exception as e:
            logger.error(f"计算分位点失败(i={i})：{e}")
    return quantiles

def get_epsTTM_data(code, start_year, end_year):
    """获取epsTTM财务数据"""
    eps_ttm_map = {}
    logger.info(f"开始查询{code}的epsTTM：年份范围{start_year-2}-{end_year}")
    try:
        query_years = range(start_year - 2, end_year + 1)
        for year in query_years:
            for quarter in [1, 2, 3, 4]:
                rs = bs.query_finance_indicator(code=code, year=year, quarter=quarter)
                if rs.error_code != '0':
                    logger.warning(f"[{code}] {year}年Q{quarter} epsTTM查询失败：{rs.error_msg}")
                    continue
                row_count = 0
                while rs.next():
                    row_count += 1
                    row = rs.get_row_data()
                    if row_count == 1:
                        logger.debug(f"[{code}] 财务接口返回字段：{rs.fields}")
                    if "epsTTM" in rs.fields:
                        eps_ttm_idx = rs.fields.index("epsTTM")
                        eps_ttm = row[eps_ttm_idx] if eps_ttm_idx < len(row) else None
                    else:
                        eps_ttm = None
                        logger.warning(f"[{code}] {year}年Q{quarter} 无epsTTM字段")
                    
                    if eps_ttm and eps_ttm != 'None' and eps_ttm != '':
                        key = f"{year}-Q{quarter}"
                        eps_ttm_map[key] = float(eps_ttm)
                        logger.debug(f"[{code}] {key} epsTTM：{eps_ttm_map[key]}")
        if not eps_ttm_map:
            logger.error(f"[{code}] 未查询到任何epsTTM数据")
        else:
            logger.info(f"[{code}] 成功查询到{len(eps_ttm_map)}条epsTTM数据")
        return eps_ttm_map
    except Exception as e:
        logger.error(f"[{code}] 获取epsTTM数据异常：{e}", exc_info=True)
        return eps_ttm_map

def match_epsTTM_by_date(date_str, eps_ttm_map, code):
    """匹配日期对应的epsTTM"""
    if not eps_ttm_map:
        logger.warning(f"[{code}] 无epsTTM数据可匹配日期：{date_str}")
        return 0.0
    try:
        date_obj = to_valid_date(date_str)
        year = date_obj.year
        month = date_obj.month
        
        quarter = "Q1" if month in [1,2,3] else "Q2" if month in [4,5,6] else "Q3" if month in [7,8,9] else "Q4"
        target_key = f"{year}-{quarter}"
        
        if target_key in eps_ttm_map:
            return eps_ttm_map[target_key]
        else:
            sorted_keys = sorted(eps_ttm_map.keys(), reverse=True)
            for key in sorted_keys:
                return eps_ttm_map[key]
        return 0.0
    except Exception as e:
        logger.error(f"[{code}] 匹配epsTTM日期失败：{e}", exc_info=True)
        return 0.0

def get_stock_data(code, start_date, end_date, is_hk=False):
    """获取股票基础数据（主函数）"""
    retry_times = 3
    for retry in range(retry_times):
        lg = None
        try:
            lg = bs.login()
            if lg.error_code != '0':
                raise Exception(f"登录失败：{lg.error_msg}")
            
            query_fields = "date,close,peTTM"
            rs = bs.query_history_k_data_plus(
                code,
                query_fields,
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="1"
            )
            
            if rs.error_code != '0':
                raise Exception(f"查询失败：{rs.error_msg}")
            
            data_list = []
            pe_list = []
            while rs.next():
                row = rs.get_row_data()
                date = row[0]
                if not date:
                    continue
                close = float(row[1]) if (row[1] and row[1] != 'None') else 0.0
                pe = float(row[2]) if (row[2] and row[2] != 'None') else 0.0
                
                data_item = {
                    "日期": date,
                    "收盘价": round_decimal(close, 2),
                    "PE": round_decimal(pe, 2),
                    "每股盈利TTM": None if is_hk else 0.0,
                    "PE分位点": 0.0,
                    "备注": "港股暂不支持epsTTM查询" if is_hk else ""
                }
                data_list.append(data_item)
                pe_list.append(pe)
            
            # 计算PE分位点
            quantiles = calculate_pe_quantile(pe_list)
            for idx, item in enumerate(data_list):
                item["PE分位点"] = quantiles[idx]
            
            # 填充epsTTM
            if not is_hk and data_list:
                start_year = int(start_date[:4])
                end_year = int(end_date[:4])
                eps_ttm_map = get_epsTTM_data(code, start_year, end_year)
                empty_eps_count = 0
                for item in data_list:
                    eps_ttm = match_epsTTM_by_date(item["日期"], eps_ttm_map, code)
                    item["每股盈利TTM"] = round_decimal(eps_ttm, 4)
                    if eps_ttm == 0.0:
                        empty_eps_count += 1
                
                # 兜底估算
                if empty_eps_count == len(data_list):
                    logger.warning(f"[{code}] 无epsTTM数据，使用PE估算")
                    for item in data_list:
                        if item["PE"] > 0 and item["收盘价"] > 0:
                            item["每股盈利TTM"] = round_decimal(item["收盘价"] / item["PE"], 4)
                            item["备注"] = "epsTTM由PE和收盘价估算"
            
            bs.logout()
            return {
                "code": 200,
                "msg": f"成功获取{len(data_list)}条数据",
                "data": data_list
            }
        except Exception as e:
            if lg:
                try:
                    bs.logout()
                except:
                    pass
            logger.error(f"重试{retry+1}次失败：{e}")
            if retry == retry_times - 1:
                return {"code": 500, "msg": f"获取数据失败：{str(e)}", "data": []}
            time.sleep(1)

# ====================== 7. 策略计算函数 ======================
def run_strategy(raw_data, params):
    """执行定投策略计算"""
    logs = []
    try:
        # 解析参数
        initial_capital = float(params.get("initialCapital", 10000))
        start_date = to_valid_date(params.get("startDate", "2023-01-01"))
        end_date = to_valid_date(params.get("endDate", datetime.datetime.now().strftime('%Y-%m-%d')))
        invest_amount = float(params.get("investAmount", 1000))
        base_ratio = float(params.get("baseRatio", 50)) / 100
        fee_rate = float(params.get("feeRate", 0.1)) / 100
        pe_lower_quantile = float(params.get("peLowerQuantile", 30))
        pe_upper_quantile = float(params.get("peUpperQuantile", 70))
        
        logs.append("开始运行策略计算（纯自有资金）...")
        
        # 过滤时间范围数据
        data = []
        for row in raw_data:
            row_date = to_valid_date(row["日期"])
            if start_date <= row_date <= end_date:
                new_row = {
                    "日期": row["日期"],
                    "收盘价": row["收盘价"],
                    "PE": row["PE"],
                    "PE分位点": row["PE分位点"],
                    "每股盈利TTM": row["每股盈利TTM"],
                    "备注": row["备注"],
                    
                    # 一次性买入
                    "一次性买入总资产": initial_capital,
                    "一次性买入收益率": 0.0,
                    "一次性买入累计收益": 0.0,
                    "一次性买入累计投入": initial_capital,
                    
                    # 普通定投
                    "普通定投总资产": 0.0,
                    "普通定投收益率": 0.0,
                    "普通定投累计收益": 0.0,
                    "普通定投累计投入": 0.0,
                    
                    # 底仓定投
                    "底仓定投总资产": initial_capital,
                    "底仓定投收益率": 0.0,
                    "底仓定投累计收益": 0.0,
                    "底仓定投累计投入": 0.0,
                    
                    # 估值止盈
                    "估值止盈总资产": initial_capital,
                    "估值止盈总收益率": 0.0,
                    "估值止盈累计收益": 0.0,
                    "估值止盈累计投入": 0.0,
                    "估值止盈仓位": 0.0,
                    "估值止盈信号": "",
                    "买卖标记": ""
                }
                data.append(new_row)
        
        if not data:
            raise ValueError("时间范围内无有效数据")
        
        logs.append(f"策略计算范围：{start_date} 至 {end_date}，共{len(data)}个交易日")
        
        # 1. 一次性买入策略
        buy_once_price = data[0]["收盘价"]
        buy_once_shares = (initial_capital * (1 - fee_rate)) / buy_once_price
        for row in data:
            row["一次性买入总资产"] = round_decimal(buy_once_shares * row["收盘价"], 2)
            row["一次性买入累计收益"] = round_decimal(row["一次性买入总资产"] - initial_capital, 2)
            row["一次性买入收益率"] = round_decimal(row["一次性买入累计收益"] / initial_capital * 100, 2)
        logs.append(f"✅ 一次性买入策略：{round_decimal(buy_once_shares, 2)}股，买入价{buy_once_price}元")
        
        # 2. 普通定投策略（修复：增加现金限制，仅用自有资金）
        normal_invest_shares = 0.0
        normal_invest_total = 0.0
        normal_invested_weeks = set()
        normal_available_cash = initial_capital  # 新增：普通定投可用现金（初始=总本金）
        
        for row in data:
            row_date = to_valid_date(row["日期"])
            current_week = get_iso_week(row_date)
            
            # 每周定投（增加现金限制：可用现金≥定投金额才定投）
            if current_week not in normal_invested_weeks and normal_available_cash >= invest_amount:
                actual_invest = min(invest_amount, normal_available_cash)  # 修复：确保不超可用现金
                buy_amount = actual_invest * (1 - fee_rate)
                normal_invest_shares += buy_amount / row["收盘价"]
                normal_invest_total += actual_invest
                normal_available_cash -= actual_invest  # 修复：扣减可用现金
                normal_invested_weeks.add(current_week)
            # 现金不足时停止定投
            elif current_week not in normal_invested_weeks and normal_available_cash < invest_amount:
                logs.append(f"⚠️ {row['日期']} 普通定投现金不足（剩余{normal_available_cash}元），停止定投")
            
            # 更新数据（修复：总资产包含剩余现金）
            row["普通定投总资产"] = round_decimal(normal_invest_shares * row["收盘价"] + normal_available_cash, 2)
            row["普通定投累计投入"] = round_decimal(normal_invest_total, 2)
            row["普通定投累计收益"] = round_decimal(row["普通定投总资产"] - initial_capital, 2)
            row["普通定投收益率"] = round_decimal(
                row["普通定投累计收益"] / normal_invest_total * 100 if normal_invest_total > 0 else 0, 2
            )
        logs.append(f"✅ 普通定投策略：累计投入{normal_invest_total}元，剩余现金{normal_available_cash}元，最终持仓{round_decimal(normal_invest_shares, 2)}股")
        
        # 3. 底仓+定投策略
        base_shares = 0.0
        float_shares = 0.0
        base_cash = initial_capital
        base_amount = initial_capital * base_ratio
        base_shares = (base_amount * (1 - fee_rate)) / data[0]["收盘价"]
        base_cash -= base_amount
        base_total_invest = base_amount
        base_invested_weeks = set()
        
        for row in data:
            row_date = to_valid_date(row["日期"])
            current_week = get_iso_week(row_date)
            
            if current_week not in base_invested_weeks and base_cash >= invest_amount:
                base_total_invest += invest_amount
                buy_amount = invest_amount * (1 - fee_rate)
                float_shares += buy_amount / row["收盘价"]
                base_cash -= invest_amount
                base_invested_weeks.add(current_week)
            
            total_shares = base_shares + float_shares
            row["底仓定投总资产"] = round_decimal(total_shares * row["收盘价"] + base_cash, 2)
            row["底仓定投累计投入"] = round_decimal(base_total_invest, 2)
            row["底仓定投累计收益"] = round_decimal(row["底仓定投总资产"] - initial_capital, 2)
            row["底仓定投收益率"] = round_decimal(
                row["底仓定投累计收益"] / initial_capital * 100, 2
            )
        logs.append(f"✅ 底仓+定投策略：初始底仓{round_decimal(base_shares, 2)}股")
        
        # 4. 估值止盈策略（修复：增加总投入上限，仅用自有资金）
        valuation_base_shares = 0.0
        valuation_float_shares = 0.0
        valuation_cash = initial_capital
        valuation_base_amount = initial_capital * 0.5
        # 新增：限制底仓金额不超初始现金
        valuation_base_amount = min(valuation_base_amount, valuation_cash)
        valuation_base_shares = (valuation_base_amount * (1 - fee_rate)) / data[0]["收盘价"]
        valuation_cash -= valuation_base_amount
        valuation_total_invest = valuation_base_amount
        buy_sell_markers = []
        
        for row in data:
            pe_quantile = row["PE分位点"]
            current_price = row["收盘价"]
            row["买卖标记"] = ""
            
            # 仓位调整（修复：增加总投入不超初始本金限制）
            if pe_quantile < pe_lower_quantile and valuation_cash > 0:
                # 新增：确保买入后总投入≤初始本金
                max_buyable = initial_capital - valuation_total_invest
                if max_buyable <= 0:
                    row["估值止盈信号"] = "满仓（总投入已达本金上限）"
                else:
                    actual_buy = min(valuation_cash, max_buyable)  # 修复：不超剩余现金+本金上限
                    buy_amount = actual_buy * (1 - fee_rate)
                    valuation_float_shares += buy_amount / current_price
                    valuation_total_invest += actual_buy
                    valuation_cash -= actual_buy
                    row["估值止盈信号"] = "加仓（PE分位点<30%）"
                    row["买卖标记"] = "buy"
                    buy_sell_markers.append({
                        "date": row["日期"],
                        "type": "buy",
                        "buyOnceReturn": row["一次性买入收益率"],
                        "normalReturn": row["普通定投收益率"],
                        "baseReturn": row["底仓定投收益率"],
                        "valuationReturn": round_decimal(
                            (row["估值止盈总资产"] - initial_capital) / initial_capital * 100, 2
                        )
                    })
                    logs.append(f"📈 {row['日期']} PE分位点{pe_quantile}%，加仓{actual_buy}元（剩余现金{valuation_cash}元）")
            
            elif pe_quantile > pe_upper_quantile and valuation_float_shares > 0:
                sell_amount = valuation_float_shares * current_price * (1 - fee_rate)
                valuation_cash += sell_amount
                valuation_float_shares = 0
                row["估值止盈信号"] = "减仓（PE分位点>70%）"
                row["买卖标记"] = "sell"
                buy_sell_markers.append({
                    "date": row["日期"],
                    "type": "sell",
                    "buyOnceReturn": row["一次性买入收益率"],
                    "normalReturn": row["普通定投收益率"],
                    "baseReturn": row["底仓定投收益率"],
                    "valuationReturn": round_decimal(
                        (row["估值止盈总资产"] - initial_capital) / initial_capital * 100, 2
                    )
                })
                logs.append(f"📉 {row['日期']} PE分位点{pe_quantile}%，减仓卖出，现金增加至{valuation_cash}元")
            
            else:
                row["估值止盈信号"] = "持有底仓（PE分位点正常）" if pe_quantile > 0 else "无PE数据"
            
            # 更新估值止盈数据
            total_valuation_shares = valuation_base_shares + valuation_float_shares
            valuation_position_value = total_valuation_shares * current_price
            row["估值止盈总资产"] = round_decimal(valuation_position_value + valuation_cash, 2)
            row["估值止盈累计投入"] = round_decimal(valuation_total_invest, 2)
            row["估值止盈累计收益"] = round_decimal(row["估值止盈总资产"] - initial_capital, 2)
            row["估值止盈总收益率"] = round_decimal(
                (row["估值止盈累计收益"] / valuation_total_invest * 100) if valuation_total_invest > 0 else 0, 2
            )
            row["估值止盈仓位"] = round_decimal(
                (valuation_position_value / row["估值止盈总资产"] * 100) if row["估值止盈总资产"] > 0 else 0, 2
            )
        
        logs.append(f"✅ 动态估值止盈策略计算完成，最终仓位{data[-1]['估值止盈仓位']}%")
        
        # 生成图表数据
        chart_data = generate_chart_data(data, buy_sell_markers)
        
        # 最终结果汇总
        final_row = data[-1]
        result_summary = {
            "一次性买入": {
                "收益率": f"{final_row['一次性买入收益率']}%",
                "收益金额": f"{final_row['一次性买入累计收益']}元"
            },
            "普通定投": {
                "收益率": f"{final_row['普通定投收益率']}%",
                "收益金额": f"{final_row['普通定投累计收益']}元"
            },
            "底仓+定投": {
                "收益率": f"{final_row['底仓定投收益率']}%",
                "收益金额": f"{final_row['底仓定投累计收益']}元"
            },
            "估值止盈": {
                "收益率": f"{final_row['估值止盈总收益率']}%",
                "收益金额": f"{final_row['估值止盈累计收益']}元",
                "最终仓位": f"{final_row['估值止盈仓位']}%"
            }
        }
        
        return {
            "success": True,
            "logs": logs,
            "result_summary": result_summary,
            "chart_data": chart_data
        }
    
    except Exception as e:
        logs.append(f"❌ 策略计算失败：{str(e)}")
        logger.error(f"策略计算异常：{e}", exc_info=True)
        return {
            "success": False,
            "logs": logs,
            "error": str(e)
        }

def generate_chart_data(data, buy_sell_markers):
    """生成前端Plotly图表数据"""
    # 提取基础数据
    dates = [row["日期"] for row in data]
    prices = [row["收盘价"] for row in data]
    pe_quantiles = [row["PE分位点"] for row in data]
    eps_ttm_values = [row["每股盈利TTM"] if row["每股盈利TTM"] is not None else 0 for row in data]
    pe_values = [row["PE"] for row in data]
    
    # 收益率数据
    buy_once_return = [row["一次性买入收益率"] for row in data]
    normal_return = [row["普通定投收益率"] for row in data]
    base_return = [row["底仓定投收益率"] for row in data]
    valuation_return = [row["估值止盈总收益率"] for row in data]
    
    # 累计投入/收益数据
    buy_once_invest = [row["一次性买入累计投入"] for row in data]
    normal_invest = [row["普通定投累计投入"] for row in data]
    base_invest = [row["底仓定投累计投入"] for row in data]
    valuation_invest = [row["估值止盈累计投入"] for row in data]
    
    buy_once_profit = [row["一次性买入累计收益"] for row in data]
    normal_profit = [row["普通定投累计收益"] for row in data]
    base_profit = [row["底仓定投累计收益"] for row in data]
    valuation_profit = [row["估值止盈累计收益"] for row in data]
    
    # 图表1：股价 vs PE分位点 vs 每股盈利TTM
    chart1 = {
        "traces": [
            {
                "x": dates,
                "y": prices,
                "name": "股价（元）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#1f77b4", "width": 2},
                "yaxis": "y1"
            },
            {
                "x": dates,
                "y": pe_quantiles,
                "name": "PE分位点（%）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#d62728", "width": 2, "dash": "dash"},
                "yaxis": "y2"
            },
            {
                "x": dates,
                "y": eps_ttm_values,
                "name": "每股盈利TTM（元）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#2ca02c", "width": 2, "dash": "dot"},
                "yaxis": "y2"
            },
            {
                "x": dates,
                "y": [30]*len(dates),
                "name": "PE下限阈值",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#2ca02c", "width": 1, "dash": "dot"},
                "yaxis": "y2"
            },
            {
                "x": dates,
                "y": [70]*len(dates),
                "name": "PE上限阈值",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#ff7f0e", "width": 1, "dash": "dot"},
                "yaxis": "y2"
            }
        ],
        "layout": {
            "title": "股价走势、PE分位点与每股盈利TTM",
            "xaxis": {
                "title": "日期",
                "type": "date",
                "tickformat": "%Y-%m-%d",
                "showgrid": True,
                "gridcolor": "#e0e0e0"
            },
            "yaxis": {
                "title": "股价（元）",
                "side": "left",
                "showgrid": True,
                "gridcolor": "#e0e0e0"
            },
            "yaxis2": {
                "title": "PE分位点（%）/ 每股盈利TTM（元）",
                "overlaying": "y",
                "side": "right",
                "showgrid": False
            },
            "legend": {"x": 0, "y": 1},
            "hovermode": "x unified",
            "height": 600
        }
    }
    
    # 图表2：收益率（含买卖点）
    chart2_traces = [
        {
            "x": dates,
            "y": buy_once_return,
            "name": "一次性买入（总收益率%）",
            "type": "scatter",
            "mode": "lines",
            "line": {"color": "#ff7f0e", "width": 2}
        },
        {
            "x": dates,
            "y": normal_return,
            "name": "普通定投（总收益率%）",
            "type": "scatter",
            "mode": "lines",
            "line": {"color": "#9467bd", "width": 2}
        },
        {
            "x": dates,
            "y": base_return,
            "name": "底仓+定投（总收益率%）",
            "type": "scatter",
            "mode": "lines",
            "line": {"color": "#2ca02c", "width": 2}
        },
        {
            "x": dates,
            "y": valuation_return,
            "name": "估值止盈（总收益率%）",
            "type": "scatter",
            "mode": "lines",
            "line": {"color": "#d62728", "width": 2}
        }
    ]
    
    # 添加买卖点标记
    if buy_sell_markers:
        buy_dates = [m["date"] for m in buy_sell_markers if m["type"] == "buy"]
        buy_vals = [m["valuationReturn"] for m in buy_sell_markers if m["type"] == "buy"]
        sell_dates = [m["date"] for m in buy_sell_markers if m["type"] == "sell"]
        sell_vals = [m["valuationReturn"] for m in buy_sell_markers if m["type"] == "sell"]
        
        chart2_traces.append({
            "x": buy_dates,
            "y": buy_vals,
            "name": "估值止盈-买入点",
            "type": "scatter",
            "mode": "markers",
            "marker": {"color": "#d62728", "size": 12, "symbol": "triangle-up"},
            "showlegend": True
        })
        
        chart2_traces.append({
            "x": sell_dates,
            "y": sell_vals,
            "name": "估值止盈-卖出点",
            "type": "scatter",
            "mode": "markers",
            "marker": {"color": "#d62728", "size": 12, "symbol": "triangle-down"},
            "showlegend": True
        })
    
    chart2 = {
        "traces": chart2_traces,
        "layout": {
            "title": "股票定投策略收益率（含买卖点标注）",
            "xaxis": {
                "title": "日期",
                "type": "date",
                "tickformat": "%Y-%m-%d",
                "showgrid": True,
                "gridcolor": "#e0e0e0"
            },
            "yaxis": {
                "title": "收益率（%）",
                "showgrid": True,
                "gridcolor": "#e0e0e0"
            },
            "legend": {"x": 0, "y": 1},
            "hovermode": "x unified",
            "height": 600
        }
    }
    
    # 图表3：累计投入 vs 累计收益
    chart3 = {
        "traces": [
            # 累计投入
            {
                "x": dates,
                "y": buy_once_invest,
                "name": "一次性买入累计投入（元）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#ff7f0e", "width": 2},
                "yaxis": "y1"
            },
            {
                "x": dates,
                "y": normal_invest,
                "name": "普通定投累计投入（元）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#9467bd", "width": 2},
                "yaxis": "y1"
            },
            {
                "x": dates,
                "y": base_invest,
                "name": "底仓定投累计投入（元）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#2ca02c", "width": 2},
                "yaxis": "y1"
            },
            {
                "x": dates,
                "y": valuation_invest,
                "name": "估值止盈累计投入（元）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#d62728", "width": 2},
                "yaxis": "y1"
            },
            # 累计收益
            {
                "x": dates,
                "y": buy_once_profit,
                "name": "一次性买入累计收益（元）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#ff7f0e", "width": 2, "dash": "dash"},
                "yaxis": "y2"
            },
            {
                "x": dates,
                "y": normal_profit,
                "name": "普通定投累计收益（元）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#9467bd", "width": 2, "dash": "dash"},
                "yaxis": "y2"
            },
            {
                "x": dates,
                "y": base_profit,
                "name": "底仓定投累计收益（元）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#2ca02c", "width": 2, "dash": "dash"},
                "yaxis": "y2"
            },
            {
                "x": dates,
                "y": valuation_profit,
                "name": "估值止盈累计收益（元）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#d62728", "width": 2, "dash": "dash"},
                "yaxis": "y2"
            }
        ],
        "layout": {
            "title": "累计投入金额 vs 累计收益",
            "xaxis": {
                "title": "日期",
                "type": "date",
                "tickformat": "%Y-%m-%d",
                "showgrid": True,
                "gridcolor": "#e0e0e0"
            },
            "yaxis": {
                "title": "累计投入金额（元）",
                "side": "left",
                "showgrid": True,
                "gridcolor": "#e0e0e0"
            },
            "yaxis2": {
                "title": "累计收益（元）",
                "overlaying": "y",
                "side": "right",
                "showgrid": False
            },
            "legend": {"x": 0, "y": 1},
            "hovermode": "x unified",
            "height": 600
        }
    }
    
    # 图表4：PE值走势
    pe_mean = np.mean([p for p in pe_values if p > 0]) if pe_values else 0
    chart4 = {
        "traces": [
            {
                "x": dates,
                "y": pe_values,
                "name": "PE(TTM)值",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#1f77b4", "width": 2}
            },
            {
                "x": dates,
                "y": [pe_mean]*len(dates),
                "name": f"PE均值（{round_decimal(pe_mean, 2)}）",
                "type": "scatter",
                "mode": "lines",
                "line": {"color": "#ff7f0e", "width": 1, "dash": "dot"}
            }
        ],
        "layout": {
            "title": "PE(TTM)值走势（含均值参考）",
            "xaxis": {
                "title": "日期",
                "type": "date",
                "tickformat": "%Y-%m-%d"
            },
            "yaxis": {
                "title": "PE(TTM)值"
            },
            "legend": {"x": 0, "y": 1},
            "hovermode": "x unified",
            "height": 600
        }
    }
    
    return {
        "chart1": chart1,
        "chart2": chart2,
        "chart3": chart3,
        "chart4": chart4
    }

# ====================== 8. API接口 ======================
@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({
        "code": 200,
        "msg": "服务器正常",
        "data": {"time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    })

@app.route('/api/stock_names', methods=['GET'])
def api_stock_names():
    """获取所有股票名称列表"""
    return jsonify({"code": 200, "msg": "success", "data": list(STOCK_NAME_MAP.keys())})

@app.route('/api/stock_by_name', methods=['GET'])
def api_stock_by_name():
    """模糊名称查询股票"""
    try:
        input_name = request.args.get('name', '').strip()
        start_date = request.args.get('start', datetime.datetime.now().strftime('%Y-%m-%d'))
        end_date = request.args.get('end', datetime.datetime.now().strftime('%Y-%m-%d'))
        
        if not input_name:
            return jsonify({
                "code": 400,
                "msg": "股票名称不能为空",
                "data": []
            })
        
        matched_stocks = fuzzy_match_stock_by_name(input_name)
        if not matched_stocks:
            return jsonify({
                "code": 404,
                "msg": f"未找到「{input_name}」相关的股票",
                "data": []
            })
        
        result_list = []
        for stock in matched_stocks:
            stock_data = get_stock_data(stock["code"], start_date, end_date, stock["is_hk"])
            result_list.append({
                "name": stock["name"],
                "code": stock["code"],
                "is_hk": stock["is_hk"],
                "stock_data": stock_data
            })
        
        return jsonify({
            "code": 200,
            "msg": f"成功匹配{len(result_list)}只股票",
            "data": result_list
        })
    
    except Exception as e:
        logger.error(f"模糊名称查询接口异常：{e}", exc_info=True)
        return jsonify({
            "code": 500,
            "msg": f"查询失败：{str(e)}",
            "data": []
        })

@app.route('/api/stock_by_letter', methods=['GET'])
def api_stock_by_letter():
    """拼音首字母查询股票"""
    try:
        input_letter = request.args.get('letter', '').strip()
        if not input_letter:
            return jsonify({
                "code": 400,
                "msg": "首字母不能为空",
                "data": []
            })
        
        matched_stocks = match_stock_by_first_letter(input_letter)
        return jsonify({
            "code": 200,
            "msg": f"成功匹配{len(matched_stocks)}只股票",
            "data": matched_stocks
        })
    
    except Exception as e:
        logger.error(f"首字母查询接口异常：{e}", exc_info=True)
        return jsonify({
            "code": 500,
            "msg": f"查询失败：{str(e)}",
            "data": []
        })

@app.route('/api/stock_name_by_code', methods=['GET'])
def api_stock_name_by_code():
    """根据代码获取股票名称"""
    try:
        stock_code = request.args.get('code', '').strip()
        if not stock_code:
            return jsonify({
                "code": 400,
                "msg": "股票代码不能为空",
                "data": {"name": "", "code": ""}
            })
        
        matched_code, is_hk = match_stock_code(stock_code)
        if not matched_code:
            return jsonify({
                "code": 404,
                "msg": f"未找到'{stock_code}'对应的股票代码",
                "data": {"name": "", "code": ""}
            })
        
        # 三级名称获取
        stock_name = CODE_TO_STOCK_NAME.get(matched_code)
        if not stock_name and not is_hk:
            stock_name = get_stock_name_from_baostock(matched_code)
        if not stock_name:
            stock_name = f"未知股票({matched_code})"
        
        return jsonify({
            "code": 200,
            "msg": "成功获取股票名称",
            "data": {
                "name": stock_name,
                "code": matched_code,
                "is_hk": is_hk
            }
        })
    
    except Exception as e:
        logger.error(f"获取股票名称接口异常：{e}", exc_info=True)
        return jsonify({
            "code": 500,
            "msg": f"接口异常：{str(e)}",
            "data": {"name": "", "code": "", "is_hk": False}
        })

@app.route('/api/stock_info', methods=['GET'])
def api_stock_info():
    """通用股票信息查询（代码/名称/首字母）"""
    try:
        input_str = request.args.get('input', '').strip()
        if not input_str:
            return jsonify({
                "code": 400,
                "msg": "输入不能为空（支持股票名称/代码/首字母）",
                "data": {"name": "", "code": "", "is_hk": False, "first_letter": ""}
            })
        
        matched_code, is_hk = match_stock_code(input_str)
        if not matched_code:
            return jsonify({
                "code": 404,
                "msg": f"未找到「{input_str}」对应的股票信息",
                "data": {"name": "", "code": "", "is_hk": False, "first_letter": ""}
            })
        
        # 三级名称获取
        stock_name = CODE_TO_STOCK_NAME.get(matched_code)
        if not stock_name and not is_hk:
            stock_name = get_stock_name_from_baostock(matched_code)
        if not stock_name:
            stock_name = f"未知股票({matched_code})"
        
        # 获取首字母
        first_letter = get_pinyin_first_letter(stock_name)
        
        return jsonify({
            "code": 200,
            "msg": "成功获取股票信息",
            "data": {
                "name": stock_name,
                "code": matched_code,
                "is_hk": is_hk,
                "first_letter": first_letter
            }
        })
    
    except Exception as e:
        logger.error(f"通用股票信息接口异常：{e}", exc_info=True)
        return jsonify({
            "code": 500,
            "msg": f"接口异常：{str(e)}",
            "data": {"name": "", "code": "", "is_hk": False, "first_letter": ""}
        })

@app.route('/api/stock_data', methods=['GET'])
def api_stock_data():
    """核心接口：获取股票基础数据"""
    try:
        stock_input = request.args.get('code', '600519')
        start_date = request.args.get('start_date', '2023-01-01')
        end_date = request.args.get('end_date', datetime.datetime.now().strftime('%Y-%m-%d'))
        
        logger.info(f"收到请求：code={stock_input}, start={start_date}, end={end_date}")
        
        stock_code, is_hk = match_stock_code(stock_input)
        if not stock_code:
            return jsonify({"code": 400, "msg": "未找到匹配的股票", "data": [], "stock_name": "", "stock_code": ""})
        
        # 三级名称获取
        stock_name = CODE_TO_STOCK_NAME.get(stock_code)
        if not stock_name and not is_hk:
            stock_name = get_stock_name_from_baostock(stock_code)
        if not stock_name:
            stock_name = f"未知股票({stock_code})"
        
        result = get_stock_data(stock_code, start_date, end_date, is_hk)
        result["stock_name"] = stock_name
        result["stock_code"] = stock_code
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"接口异常：{e}", exc_info=True)
        return jsonify({"code": 500, "msg": f"接口异常：{str(e)}", "data": [], "stock_name": "", "stock_code": ""})

@app.route('/api/analyze_strategy', methods=['POST'])
def api_analyze_strategy():
    """核心接口：定投策略分析"""
    try:
        params = request.json
        logger.info(f"收到策略分析请求：{params}")
        
        # 获取基础股票数据
        stock_code, is_hk = match_stock_code(params.get('stockCode', '600519'))
        if not stock_code:
            return jsonify({
                "success": False,
                "logs": ["❌ 未找到匹配的股票"],
                "error": "未找到匹配的股票"
            })
        
        # 三级名称获取
        stock_name = CODE_TO_STOCK_NAME.get(stock_code)
        if not stock_name and not is_hk:
            stock_name = get_stock_name_from_baostock(stock_code)
        if not stock_name:
            stock_name = f"未知股票({stock_code})"
        
        stock_data_result = get_stock_data(
            stock_code,
            params.get('baostockStartDate', '2023-01-01'),
            params.get('baostockEndDate', datetime.datetime.now().strftime('%Y-%m-%d')),
            is_hk
        )
        
        if stock_data_result["code"] != 200 or not stock_data_result["data"]:
            return jsonify({
                "success": False,
                "logs": [f"❌ 获取股票数据失败：{stock_data_result['msg']}"],
                "error": stock_data_result["msg"]
            })
        
        # 执行策略计算
        strategy_result = run_strategy(stock_data_result["data"], params)
        strategy_result["stock_name"] = stock_name
        strategy_result["stock_code"] = stock_code
        
        return jsonify(strategy_result)
    
    except Exception as e:
        logger.error(f"分析接口异常：{e}", exc_info=True)
        return jsonify({
            "success": False,
            "logs": [f"❌ 接口异常：{str(e)}"],
            "error": str(e)
        })

@app.route('/api/login', methods=['POST'])
def api_login():
    """Mock登录接口"""
    try:
        data = request.json
        username = data.get('username', '')
        password = data.get('password', '')
        
        logger.info(f"Mock登录请求：用户名={username}")
        return jsonify({
            "code": 200,
            "msg": "登录成功（预留功能）",
            "data": {
                "token": f"mock_token_{username}_{int(time.time())}",
                "username": username
            }
        })
    except Exception as e:
        logger.error(f"Mock登录接口异常：{e}")
        return jsonify({
            "code": 500,
            "msg": "登录失败（预留功能）",
            "data": {}
        })

# ====================== 9. 启动配置（兼容本地/宝塔） ======================
if __name__ == '__main__':
    # 预连接Baostock测试
    try:
        test_login = bs.login()
        if test_login.error_code == '0':
            logger.info("Baostock预连接成功")
            bs.logout()
        else:
            logger.warning(f"Baostock预连接失败：{test_login.error_msg}")
    except Exception as e:
        logger.error(f"Baostock预连接异常：{e}")
    
    # 启动服务（生产环境建议用uWSGI，本地调试用这个）
    app.run(
        host='0.0.0.0',    # 允许外部访问
        port=8002,         # 端口（和Nginx反向代理一致）
        debug=False,       # 生产环境关闭debug
        threaded=True,     # 开启多线程
        use_reloader=False # 关闭自动重载
    )