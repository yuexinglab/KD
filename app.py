from flask import Flask, render_template, request, redirect, session
import json
import os
import time
import tempfile
from uuid import uuid4
from dotenv import load_dotenv
from openai import OpenAI
from rapidocr_onnxruntime import RapidOCR

app = Flask(__name__)
app.secret_key = "fanzhaxia_secret_key_123"

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not DEEPSEEK_API_KEY:
    raise ValueError("没有读取到 DEEPSEEK_API_KEY，请检查 .env 文件")

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

ocr_engine = RapidOCR()

SCORES_FILE = "scores.json"
CASES_FILE = "cases.jsonl"
GOOD_DEEDS_FILE = "good_deeds.jsonl"
GOOD_UPLOAD_FOLDER = "uploads_good"

# 今日善意任务（先写死，后面再升级成每日切换）
TODAY_KINDNESS_TASK = {
    "title": "提醒身边人注意防诈骗",
    "description": "今天请记录一次你提醒家人、邻居、朋友或其他人注意诈骗风险的行为。比如提醒别人不要点陌生链接、不要发验证码、不要轻易转账。"
}


def ensure_file_exists(file_path, default_content=None):
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            if default_content is not None:
                if isinstance(default_content, (dict, list)):
                    json.dump(default_content, f, ensure_ascii=False, indent=2)
                else:
                    f.write(default_content)


def ensure_upload_folder():
    os.makedirs(GOOD_UPLOAD_FOLDER, exist_ok=True)


def load_scores():
    ensure_file_exists(SCORES_FILE, {})
    with open(SCORES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_scores(scores):
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)


def get_user_score(username):
    scores = load_scores()
    if username not in scores:
        scores[username] = 0
        save_scores(scores)
    return scores[username]


def add_score(username):
    scores = load_scores()
    if username not in scores:
        scores[username] = 0
    scores[username] += 1
    save_scores(scores)
    return scores[username]


def use_score(username):
    scores = load_scores()
    if username not in scores:
        scores[username] = 0

    if scores[username] > 0:
        scores[username] -= 1
        save_scores(scores)
        return True, scores[username]
    else:
        return False, scores[username]


def extract_text_from_image(image_file):
    if not image_file or not image_file.filename:
        return ""

    suffix = "." + image_file.filename.rsplit(".", 1)[-1] if "." in image_file.filename else ".png"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        image_file.save(temp_file.name)
        temp_path = temp_file.name

    try:
        result, _ = ocr_engine(temp_path)

        if not result:
            return ""

        texts = []
        for item in result:
            if len(item) >= 2:
                texts.append(item[1])

        return "\n".join(texts).strip()

    except Exception as e:
        return f"OCR识别失败：{str(e)}"


def save_uploaded_good_image(image_file):
    """
    保存善意端上传的图片，返回保存后的文件名
    """
    if not image_file or not image_file.filename:
        return ""

    ensure_upload_folder()

    original_name = image_file.filename
    ext = ""
    if "." in original_name:
        ext = "." + original_name.rsplit(".", 1)[-1].lower()

    safe_filename = f"{int(time.time())}_{uuid4().hex[:8]}{ext}"
    save_path = os.path.join(GOOD_UPLOAD_FOLDER, safe_filename)
    image_file.save(save_path)

    return safe_filename


def analyze_scam(user_text):
    system_prompt = """
你是“反诈虾”，一个专门帮助普通人（尤其是中老年用户）识别诈骗风险的AI助手。

你的首要目标不是“判断得中立”，而是“优先保护用户不要误点、误信、误转账”。

请严格遵守以下规则：

1. 使用简单、通俗、直接的中文，不要讲复杂术语。
2. 风险等级只能是：高风险 / 中风险 / 低风险 / 暂时无法判断
3. 只要出现以下任一情况，原则上不要轻易给“低风险”：
   - 陌生链接
   - 红包链接、优惠券链接、领奖链接
   - 扫码
   - 下载APP
   - 加群、拉群、听课、导师带单、内部消息
   - 要求提供验证码、密码、身份证、银行卡信息
   - 催促马上操作、马上点击、马上领取
   - 自称客服、平台、银行、公检法、熟人借钱但无法核实身份
4. 对“红包、优惠券、福利领取、免费赠送、扫码领礼品”这类内容，即使不一定百分之百是诈骗，也应默认提醒用户不要直接点击，通常至少评为“中风险”。
5. 对“拉群学习、分享笔记、投资群、兼职群、刷单群、带单群”要高度警惕，通常优先考虑诈骗、诱导营销或收割。
6. 如果内容看起来像正规平台活动，也必须提醒用户只通过官方App或官方入口核实，不要直接点陌生消息里的链接。
7. 你不是普通聊天助手，你是防诈助手。宁可偏谨慎，也不要轻易放松警惕。
8. 建议必须明确可执行。
9. 你必须只返回合法 JSON，不要返回任何解释文字，不要加 markdown 代码块。
10. short_tip 必须是一句很短、很直接、适合老人立即看懂的话，长度尽量控制在 10 到 25 个字之间。

返回格式必须严格如下：

{
  "risk_level": "高风险/中风险/低风险/暂时无法判断",
  "scam_type": "诈骗类型",
  "short_tip": "一句最短提醒",
  "reason": ["原因1", "原因2", "原因3"],
  "advice": ["建议1", "建议2", "建议3"]
}
"""

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请判断下面这段内容是否可能有诈骗风险：\n\n{user_text}"}
        ],
        temperature=0.2,
        response_format={"type": "json_object"}
    )

    content = response.choices[0].message.content
    return json.loads(content)


def analyze_good_deed(task_title, task_description, good_text):
    """
    善意端 AI 判断：
    重点不是做道德审判，而是温和判断用户提交的内容
    是否符合今日善意任务、是否足够具体，能否记为有效善意记录
    """
    system_prompt = f"""
你是“反诈虾”里的善意小助手，负责温和判断用户提交的善意内容是否符合今天的善意任务。

今天的善意任务是：
标题：{task_title}
说明：{task_description}

请严格遵守以下规则：

1. 你不是审判者，不要指责用户，不要阴阳怪气，不要冷冰冰。
2. 你的目标是鼓励、引导、放大善意，同时保持一个最基本的门槛。
3. 你要判断的不是“这个人是不是好人”，而是“这段提交内容是否足够具体，并且是否符合今天的善意任务”。
4. 如果用户确实描述了一个与今日任务相关的具体行为，通常应判定为有效。
5. 如果内容太空泛、太模糊、完全看不出做了什么，或者明显与今日任务无关，可以判定为无效。
6. 即使判定为无效，也必须语气温和，鼓励用户补充，而不是打击用户。
7. 使用简单、通俗、温和的中文。
8. 你必须只返回合法 JSON，不要返回任何解释文字，不要加 markdown 代码块。
9. encouragement 必须是一句自然、温和、鼓励的话。
10. kindness_type 尽量概括这条善意的类型；如果看不出来，就写“暂不明确”。

请严格返回以下格式：

{{
  "is_valid": true,
  "kindness_type": "善意类型",
  "encouragement": "一句温和鼓励的话",
  "reason": ["原因1", "原因2"],
  "suggestion": ["建议1", "建议2"]
}}
"""

    user_prompt = f"""
请根据今天的善意任务，判断下面这段提交内容是否可以记为一次有效善意：

{good_text}
"""

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
        response_format={"type": "json_object"}
    )

    content = response.choices[0].message.content
    return json.loads(content)


def save_case(username, input_type, input_text, result_data):
    record = {
        "username": username,
        "input_type": input_type,   # text / ocr_image
        "input_text": input_text,
        "risk_level": result_data.get("risk_level", ""),
        "scam_type": result_data.get("scam_type", ""),
        "short_tip": result_data.get("short_tip", ""),
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        with open(CASES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"保存案例失败：{e}")


def save_good_deed_record(username, task_info, good_text, uploaded_filename, result_data, score_added):
    record = {
        "username": username,
        "task_title": task_info.get("title", ""),
        "task_description": task_info.get("description", ""),
        "good_deed_text": good_text,
        "has_image": bool(uploaded_filename),
        "image_filename": uploaded_filename,
        "is_valid": result_data.get("is_valid", False),
        "kindness_type": result_data.get("kindness_type", ""),
        "encouragement": result_data.get("encouragement", ""),
        "reason": result_data.get("reason", []),
        "suggestion": result_data.get("suggestion", []),
        "score_added": score_added,
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        with open(GOOD_DEEDS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"保存善意记录失败：{e}")


def test_ai():
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "你是一个简洁、友好的助手。"},
            {"role": "user", "content": "请用一句中文介绍你自己。"}
        ],
        temperature=0.7
    )

    return response.choices[0].message.content


@app.route("/test_ai")
def test_ai_page():
    try:
        result = test_ai()
        return f"<h1>AI 测试成功</h1><p>{result}</p>"
    except Exception as e:
        return f"<h1>AI 测试失败</h1><p>{str(e)}</p>"


@app.route("/", methods=["GET", "POST"])
def index():
    if "username" in session:
        return redirect(f"/home/{session['username']}")

    if request.method == "POST":
        username = request.form.get("username", "").strip()

        if username:
            session["username"] = username
            return redirect(f"/home/{username}")

    return render_template("index.html")


@app.route("/home/<username>")
def home_page(username):
    score = get_user_score(username)
    return render_template("home.html", username=username, score=score)


@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect("/")


@app.route("/score/<username>", methods=["GET", "POST"])
def score_page(username):
    score_result_text = ""
    good_result_data = None
    score_added = False
    uploaded_filename = ""

    task_info = TODAY_KINDNESS_TASK

    if request.method == "POST":
        good_deed = request.form.get("good_deed", "").strip()
        image_file = request.files.get("image_file")

        has_text = bool(good_deed)
        has_image = bool(image_file and image_file.filename)

        if not has_text and not has_image:
            score_result_text = "你还没有填写善意说明，也没有上传图片。"
        elif not has_text:
            score_result_text = "请至少写一句说明，例如：我今天提醒妈妈不要点陌生链接。"
        else:
            try:
                # 先保存图片（如果有）
                if has_image:
                    uploaded_filename = save_uploaded_good_image(image_file)

                # AI 判断是否符合今日任务
                good_result_data = analyze_good_deed(
                    task_title=task_info["title"],
                    task_description=task_info["description"],
                    good_text=good_deed
                )

                # 通过才加分
                if good_result_data.get("is_valid") is True:
                    add_score(username)
                    score_added = True
                    score_result_text = "这条善意已被记录，并获得 1 分。"
                else:
                    score_result_text = "这条内容暂时还不能记分，不过你可以根据提示再补充一下。"

                # 保存记录
                save_good_deed_record(
                    username=username,
                    task_info=task_info,
                    good_text=good_deed,
                    uploaded_filename=uploaded_filename,
                    result_data=good_result_data,
                    score_added=score_added
                )

            except Exception as e:
                score_result_text = f"处理失败：{str(e)}"

    score = get_user_score(username)
    return render_template(
        "score.html",
        username=username,
        score=score,
        score_result_text=score_result_text,
        good_result_data=good_result_data,
        score_added=score_added,
        uploaded_filename=uploaded_filename,
        task_info=task_info
    )


@app.route("/use/<username>", methods=["GET", "POST"])
def use_page(username):
    result_text = ""
    result_data = None
    used_successfully = False
    uploaded_filename = ""
    ocr_text = ""

    if request.method == "POST":
        user_text = request.form.get("user_text", "").strip()
        image_file = request.files.get("image_file")

        if image_file and image_file.filename:
            uploaded_filename = image_file.filename

        if not user_text and not uploaded_filename:
            result_text = "你还没有输入文字，也没有上传图片。"
        else:
            current_score = get_user_score(username)

            if current_score <= 0:
                result_text = "当前善意分不足，请先去善意加分页获得积分，再回来使用。"
            else:
                try:
                    # 情况1：只上传图片，不输入文字
                    if uploaded_filename and not user_text:
                        ocr_text = extract_text_from_image(image_file)

                        if not ocr_text:
                            result_text = "图片已收到，但暂时没有识别出文字。"
                        elif ocr_text.startswith("OCR识别失败："):
                            result_text = ocr_text
                        else:
                            result_data = analyze_scam(ocr_text)
                            save_case(username, "ocr_image", ocr_text, result_data)
                            use_score(username)   # AI 成功后，再扣 1 分
                            used_successfully = True
                            result_text = "已根据截图内容完成分析。"

                    # 情况2：输入了文字（不管有没有图片，先按文字分析）
                    elif user_text:
                        result_data = analyze_scam(user_text)
                        save_case(username, "text", user_text, result_data)
                        use_score(username)   # AI 成功后，再扣 1 分
                        used_successfully = True

                except Exception as e:
                    result_text = f"处理失败：{str(e)}"

    score = get_user_score(username)
    return render_template(
        "use.html",
        username=username,
        score=score,
        result_text=result_text,
        result_data=result_data,
        used_successfully=used_successfully,
        uploaded_filename=uploaded_filename,
        ocr_text=ocr_text
    )


if __name__ == "__main__":
    ensure_upload_folder()
    ensure_file_exists(SCORES_FILE, {})
    ensure_file_exists(GOOD_DEEDS_FILE, "")
    app.run(debug=True, host="0.0.0.0", port=5000)