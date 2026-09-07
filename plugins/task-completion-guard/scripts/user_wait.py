"""Recognize explicit user-decision handoffs without treating them as completion.

This fallback reads only the current assistant's visible final prose. Structured
needs_user declarations remain the primary protocol; quotations, examples and
mere mentions of confirmation are deliberately insufficient.
"""
import re

MAX_MESSAGE_BYTES = 64 * 1024
DECISION_ZH = r"(?:确认|评审|审阅|审核|批准|审批|决定|选择|反馈|答复|回复|授权|同意)"
WAIT_ZH = re.compile(r"(?:等待|等候|等着|等|待)(?:你|您|用户)(?:[^。！？!?\n]{0,72}?)" + DECISION_ZH)
REQUEST_ZH = re.compile(r"(?:请(?:你|您)?|需要(?:你|您|用户))(?:[^。！？!?\n]{0,72}?)" + DECISION_ZH)
PAUSE_ZH = re.compile(r"(?:我(?:们)?|本轮|当前|目前|现阶段|这一步|这一阶段)[^。！？!?\n]{0,50}(?:暂停|停在|停留|停止|保持在|先停|等待|等你|等您)")
NEGATIVE_ZH = re.compile(r"(?:不需要|无需|无须|不用|不必|不要|不用再|无需再|不再|不能|不应|不|别)(?:[^，,。；;]{0,12})$")
CONDITIONAL_ZH = re.compile(r"(?:如果|假如|假设|若|例如|比如|举例|示例|示意|一旦|当你|当您)")
DONE_ZH = re.compile(r"(?:已(?:经)?(?:获得|得到|收到)?|此前(?:已)?|之前(?:已)?)(?:你|您|用户)?(?:的)?(?:确认|批准|授权|同意|回复)")
WAIT_EN = re.compile(r"\b(?:waiting\s+for|awaiting|need(?:ing)?|requires?)\s+(?:your|the user's)\s+[^.!?\n]{0,72}?\b(?:approval|confirmation|decision|choice|selection|feedback|review|input|answer|permission)\b", re.I)
PAUSE_EN = re.compile(r"\b(?:I(?:'m| am| will)?|we(?:'re| are| will)?)\s+(?:currently\s+|now\s+|remain\s+)?(?:paus(?:e[ds]?|ing)|stop(?:ped|ping)?|wait(?:ing)?)\b", re.I)
NEGATIVE_EN = re.compile(r"\b(?:not|never|no longer|don't|doesn't|do not|does not|without|won't|will not)\b", re.I)
CONDITIONAL_EN = re.compile(r"\b(?:if|when|unless|example|sample|imagine|suppose|would|might|could)\b", re.I)


def visible_prose(message):
    if not isinstance(message, str) or len(message.encode('utf-8')) > MAX_MESSAGE_BYTES:
        return ''
    message = re.sub(r'<!--.*?-->', '', message, flags=re.S)
    lines, fence = [], None
    for line in message.splitlines():
        stripped = line.lstrip()
        match = re.match(r'(`{3,}|~{3,})', stripped)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1]:
                fence = None
            continue
        if fence is not None or stripped.startswith('>') or line.startswith(('    ', '\t')):
            continue
        lines.append(line)
    prose = '\n'.join(lines)
    prose = re.sub(r'`+[^`\n]*`+', '', prose)
    prose = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"', '', prose)
    prose = re.sub(r"(?<!\w)'(?:[^'\n]|(?<=\w)'(?=\w))*'(?!\w)", '', prose)
    prose = re.sub(r'!\[[^\]]*\]\([^\n]*?\)', '', prose)
    prose = re.sub(r'\[([^\]]+)\]\([^\n]*?\)', r'\1', prose)
    return prose.replace('**', '').replace('__', '')


DESCRIPTION_ZH = re.compile(r"日志|文档|示例|示意|示范|字符串|输出|界面|流程|系统|函数|脚本|按钮|代码|注释|网页")
RESUME_AFTER_ZH = re.compile(r"(?:确认|批准|授权|决定|选择)[^。！？!?\n]{0,10}后[^。！？!?\n]{0,14}(?:再|才)[^。！？!?\n]{0,8}(?:继续|执行|实现|修改)|(?:我|我们)[^。！？!?\n]{0,8}在(?:你|您)[^。！？!?\n]{0,24}后[^。！？!?\n]{0,8}继续")
CONTINUES_ZH = re.compile(r"(?:与此同时|同时|仍然|仍在|现在)[^。！？!?\n]{0,16}(?:继续|执行|实现|修改)")
CANCELLED_WAIT_ZH = re.compile(r"的(?:环节|流程|步骤|要求|请求|等待)[^。！？!?\n]{0,12}(?:取消|结束|不再需要)|(?:不再|无需再|不用再)[^。！？!?\n]{0,8}(?:等待|确认)")
UNTIL_EN = re.compile(r"\buntil\s+you\s+(?:confirm|approve|decide|choose|select|authorize)\b", re.I)



def own_pause(text):
    for match in PAUSE_ZH.finditer(text):
        if re.search(r"不|未|没|无需|不用|无须|别", match.group(0)):
            continue
        # "当前" embedded in "系统当前停在..." describes the system, not
        # a decision handoff by this assistant.
        if (match.start() and text[match.start() - 1].isalnum()
                and not match.group(0).startswith("我")):
            continue
        return True
    return False


def continuing_now(text):
    pattern = r"(?:我|我们)(?:现在|仍会|仍然|仍在|会)?(?:先)?(?:继续|还要)(?:完成|执行|实现|修改|处理|检查|验证|测试)"
    for match in re.finditer(pattern, text):
        before = text[max(0, match.start() - 24):match.start()]
        if not re.search(r"(?:确认|批准|授权|决定|选择)[^。！？!?\n]{0,8}后", before):
            return True
    return False


def clarification_only(prompt):
    """A question while parked asks for information, not implicit approval."""
    if not isinstance(prompt, str):
        return False
    text = prompt.strip()
    return bool(re.search(r"^(?:为什么|为何|什么|哪里|哪种|哪一|怎样|如何|怎么|能否解释|可以解释|能解释|解释一下|什么时候)|[?？]$|^(?:why|what|how|where|when|can you explain|could you explain)\b", text, re.I))


def waiting_for_user(message):
    prose = visible_prose(message)
    sentences = [part.strip(' \t-*#') for part in re.split(r'[。！？!?\n]+', prose)]
    for index, text in enumerate(sentences):
        if not text:
            continue
        if continuing_now(text):
            continue
        previous = sentences[index - 1] if index else ''
        pause_context = previous + ' ' + text
        for match in WAIT_ZH.finditer(text):
            before, after = text[:match.start()], text[match.end():]
            clause = re.split(r'[，,；;]', before)[-1].strip()
            if (NEGATIVE_ZH.search(before) or CONDITIONAL_ZH.search(before)
                    or re.match(r"(?:并)?(?:不是|并非|不再是|不是当前)", after)):
                continue
            if (DESCRIPTION_ZH.search(before) and not own_pause(before)) or CANCELLED_WAIT_ZH.search(after) or CONTINUES_ZH.search(after):
                continue
            if re.search(r"(?:之前|以前)[^。！？!?\n]{0,20}(?:我|我们)[^。！？!?\n]{0,12}(?:先|继续|还要)[^。！？!?\n]{0,30}(?:完成|执行|实现|修改|补齐|检查|验证|测试|处理)", after):
                continue
            if not clause or re.fullmatch(r'(?:我(?:们)?)(?:正在|现在|目前|会先|先|在|继续)?', clause) or own_pause(before):
                return True
        for match in REQUEST_ZH.finditer(text):
            before = text[:match.start()]
            if (NEGATIVE_ZH.search(before) or CONDITIONAL_ZH.search(before)
                    or DESCRIPTION_ZH.search(before) or DONE_ZH.search(before)):
                continue
            if own_pause(pause_context) or RESUME_AFTER_ZH.search(text):
                return True
        for match in WAIT_EN.finditer(text):
            before, after = text[:match.start()], text[match.end():]
            if NEGATIVE_EN.search(before) or CONDITIONAL_EN.search(text):
                continue
            if re.search(r"\b(?:is|was)\s+(?:not|no longer)|\b(?:cancelled|canceled|no longer needed)\b", after, re.I):
                continue
            if (not before.strip() or re.fullmatch(r"(?:I(?:'m| am)?|we(?:'re| are)?)\s*(?:currently|now|still)?\s*", before, re.I)
                    or PAUSE_EN.search(before)):
                return True
        if UNTIL_EN.search(text) and PAUSE_EN.search(text) and not NEGATIVE_EN.search(text) and not CONDITIONAL_EN.search(text):
            return True
    return False
