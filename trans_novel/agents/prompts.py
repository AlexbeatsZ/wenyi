"""提示词模板（多源语言 → 中文）。

模板用 string.Template（$ 占位），避免与 JSON 示例里的花括号冲突。
语言相关片段用 $src_label / $lang_guidance / $term_guidance 占位，
render() 按 src 自动注入 langprofile 默认值（调用方可显式覆盖）。

缓存约定（命中 DeepSeek 自动前缀缓存，命中部分输入价≈0.1×）：
- system 模板必须全静态（一次运行内恒定）——勿放每批变化的量（如段数 $n、按批裁剪的术语表）；
  段数等约束写在 user 末尾。这样 system 成为所有同类调用共享的前缀。
- user 模板按"静态→动态"排列：风格指南/全书概览(书级恒定) → 本章梗概(章级恒定) →
  专有名词表(批级可能刷新) → 前文译文(每批变) → 待译正文(每批变)。前缀越长且越稳定，命中越多。
"""

from __future__ import annotations

from string import Template

from ..glossary.store import GlossaryTerm
from . import langprofile

_PUNCT_COMMON = (
    "在不违反当前任务其它明确格式要求的前提下，保留输入文本中标点与符号的结构作用；"
    "除句号、逗号等普通句读可按中文语序调整外，引号、括号、问号、叹号、冒号、分号、"
    "破折号、省略号、间隔号、波浪号、斜杠、星号、音符及其他特殊符号均不得遗漏，"
    "并保持其位置、层级、数量、重复形式和配对关系。"
)

PUNCT_RULE_ZH_CN = _PUNCT_COMMON + (
    "标点务必转换为简体中文大陆通用全角形式：句读用 ，。！？：；、，"
    "引号用 “”‘’，省略号用 ……，破折号用 ——；"
    "不得使用半角标点，也不要保留日式「」『』或英式直引号。"
    "若一个输入逻辑段以日式「开头并以」结尾，译文必须以中文“开头并以”结尾；"
    "即使语境已经能辨认说话人，也绝不可省略这对外层对话引号。"
)

PUNCT_RULE_SOURCE = _PUNCT_COMMON + (
    "普通句读使用中文全角形式（，。！？：；、，省略号用 ……，破折号用 ——）；"
    "引号样式必须服从原文：原文使用直角引号「」和双直角引号『』时，译文也原样沿用，"
    "外层、内层不得互换，不得改成弯引号“”‘’，也不得因中文语境已经清楚而省略。"
    "若一个输入逻辑段以「开头并以」结尾，译文必须同样以「开头并以」结尾；"
    "若以『开头并以』结尾，也必须原样保持。"
)

# 兼容外部导入；默认采用本项目推荐的“跟随原文”策略。
PUNCT_RULE = PUNCT_RULE_SOURCE


def punctuation_rule(quote_style: str) -> str:
    """按配置返回翻译、润色与标题翻译共用的引号规则。"""
    return PUNCT_RULE_ZH_CN if quote_style == "zh-cn" else PUNCT_RULE_SOURCE

# ── 默认模板 ───────────────────────────────────────────────────────────────
TRANSLATOR_SYSTEM = Template("""\
你是一位资深的文学翻译，精通将$src_label小说翻译为简体中文，专精长篇小说/轻小说。严格遵守：
1. 忠实原文，绝不漏译、增译，绝不合并或拆分段落；保留原文分段。
2. 输入是带编号的$src_label段落数组。必须输出等长的中文译文数组（数量与输入段落严格相等），
   顺序、数量与输入严格一一对应；第 i 个译文对应第 i 段原文。
3. 【专有名词对照表】是全书对照表的**相关子集参考**，可能含本批未出现的词条：**只有当某词条原文确实出现在
   本批待译段落里，才套用其固定译法**，切勿把与本批无关的词条硬塞进译文。已列词条全书统一用其译法；
   表中未列的专名，沿用【前文回顾】中已出现的译法，勿另起译名。
4. 【当前位置允许使用的人物事实】经过证据与时序过滤，可正常用于代词和身份判断；不得把其它后文知识当成当前已知事实。
   参考可用的【全书概览】与【本章梗概】把握脉络；参考【前文译文】保持代词、称谓、语气与跨段句意自然连贯。
5. 源语言相关要点：
$lang_guidance
6. 保留原文语气与文体；**严格执行【风格指南】给出的叙事人称、句式节奏与语域**；
   对话按角色的口癖/自称习惯译出辨识度；心理、修辞按中文小说习惯自然表达，不生硬直译、不堆砌翻译腔。
7. $punct_rule
8. 仅输出 JSON 对象：{"translations": ["第0段译文", "第1段译文", ...]}，不要任何解释或思考过程。\
""")

TRANSLATOR_USER = Template("""\
【角色信息 / 风格指南】
$style

【当前位置允许使用的人物事实】
$narrative_facts

【全书概览】
$book_synopsis

【本章梗概】
$chapter_digest

【专有名词对照表】（必须遵守）
$glossary

【前文译文（最近）】
$context

【待译$src_label段落】（共 $n 段，编号 0 至 ${n_minus_1}）
$numbered_source

请翻译以上每一段，输出 JSON：{"translations":[...]}，数组长度必须恰好为 $n。\
""")

TRANSLATOR_FIX_USER = Template("""\
【角色信息 / 风格指南】
$style

【当前位置允许使用的人物事实】
$narrative_facts

【全书概览】
$book_synopsis

【本章梗概】
$chapter_digest

【专有名词对照表】（必须遵守）
$glossary

【前文译文】
$context_before

【后文译文】
$context_after

【审校意见】（首译存在的问题，重译必须修正）
$feedback

【待重译$src_label段落】（仅 1 段）
[0] $source

请重译该段，完整传达原文全部信息并与前后文衔接，输出 JSON：{"translations":["译文"]}，数组长度恰为 1。\
""")

REVIEWER_SYSTEM = Template("""\
你是严格的译文审校，比对$src_label原文与$tgt_label译文，逐段找出**确凿**的问题。问题类型：
- missing：漏译（原文有的信息译文缺失）
- added：增译（译文凭空增加原文没有的信息）
- mistranslation：误译/误读原意
- terminology：原文确实出现、且对照表已给固定译法的词，译文未遵守
  （对照表为全书参考，含本批未出现的词条；只就本批原文实际出现的词判断，勿因表中无关词条误报）
- pronoun：人称/性别代词错误
性别/人称只有在当前原文明示，或【当前位置允许使用的人物事实】提供已经确认的性别事实时才能判错；姓名印象、外貌、服装、语气、
第一人称和全书后文只能算弱证据。原文有伪装、误认、悬念或未揭示身份时，译文保持姓名/称谓/省略主语是正确做法，
不得依据后文真相要求早期译文提前改用会泄露身份的代词。
只报实质性错误：合理的语序调整、自然意译、风格润色**不算问题**，不要报。
拿不准是否为错就不报，宁缺毋滥。每条须给出可直接采纳的 suggestion。仅输出 JSON：
{"issues":[{"index":整数段号,"type":"...","detail":"简述","suggestion":"修改后的译文或具体改法"}]}
没有问题则输出 {"issues":[]}。\
""")

REVIEWER_USER = Template("""\
【当前位置允许使用的人物事实】
$narrative_facts

【专有名词对照表】
$glossary

【逐段对照】（共 $n 段）
$pairs

请审校并输出 JSON：{"issues":[...]}。\
""")

GLOSSARY_ARBITER_SYSTEM = Template("""\
你是长篇小说翻译项目的术语冲突裁定编辑。对每个$src_label原词，结合词条类型、候选中文译名和
原文/现有译文上下文，选出全书后续统一使用的简体中文译名。优先选择候选中最准确、自然且符合
人物身份与既有文风的一项；只有候选均明显错误时才提出更合适的译名。不得遗漏、合并或虚构原词，
每个输入 source 必须恰好返回一次。仅输出 JSON：
{"decisions":[{"source":"原词","target":"最终译名","reason":"简短理由"}]}。\
""")

GLOSSARY_ARBITER_USER = Template("""\
【待裁定术语冲突】
$items

请逐项裁定并输出 JSON：{"decisions":[...]}。\
""")

POLISHER_SYSTEM = Template("""\
你是$src_label小说的中文润色编辑兼精修译者。逐段对照原文和初译，在不改变原意、不增删信息的前提下：
修正漏译、误译、指代和语气问题，提升中文流畅度与文学性，并结合全书、章节和前文上下文保持衔接。
人物性别由当前原文明示或对照表确认时，应自然使用正确代词，不要机械回避。若当前叙事以多条一致线索自然呈现人物性别，
且没有身份悬念、矛盾或反转信号，也可沿用当前呈现；但单一的姓名印象、外貌、服装、语气或第一人称不能锁定全书事实。
只有证据单一/冲突或作品刻意隐藏身份时，才优先重复姓名/称谓、自然省略主语或使用不暴露性别的表达。全书概览中的后文真相不得提前泄露，
须保留当前章节的伪装、误认、悬念和叙事视角。
务必保持段数不变、与输入一一对应；初译正确时不要为了改写而改写。
严格沿用【专有名词对照表】的固定译法（表为全书参考，仅就译文实际涉及的词沿用，勿塞入无关词条）。$punct_rule
仅输出 JSON：{"polished":["第0段","第1段",...]}，长度与输入段数相等。\
""")

POLISHER_USER = Template("""\
【角色信息 / 风格指南】
$style

【当前位置允许使用的人物事实】
$narrative_facts

【全书概览】
$book_synopsis

【本章梗概】
$chapter_digest

【专有名词对照表】
$glossary

【前文最终译文（最近）】
$context

【待精修原文与初译】（共 $n 段）
$pairs

输出 JSON：{"polished":[...]}，长度恰为 $n。\
""")

TITLE_TRANSLATOR_SYSTEM = Template("""\
你是$src_label小说的标题翻译。把【章节标题与目录项】逐条翻译为简体中文：
1. 输入依次为各章标题或额外目录项标题（带编号），不包含书名。
2. 必须输出等长的中文数组（数量与输入条数严格相等），顺序一一对应。
3. 严格遵守【专有名词对照表】的固定译法（人名/地名/术语全书一致）。
4. 标题须简洁、合乎中文书名/章节命名习惯；不加引号、书名号或解释；
   形如「第3章」「序章」「エピローグ」之类的卷章序号/通用标记，按中文惯例翻译
   （如「第3章」「序章」「尾声」），不要音译。
5. $punct_rule
仅输出 JSON：{"titles":["第0条标题译文","第1条标题译文",...]}，长度与输入条数相等。\
""")

TITLE_TRANSLATOR_USER = Template("""\
【专有名词对照表】
$glossary

【待译标题】（共 $n 条）
$numbered_titles

输出 JSON：{"titles":[...]}，长度恰为 $n。\
""")

ANALYZER_SYSTEM = Template("""\
你是小说翻译项目的前期分析师。阅读以下$src_label样章，产出供后续翻译统一遵循的基准信息。
术语字段说明：$term_guidance
仅输出 JSON：
{
  "genre": "体裁",
  "tone": "整体语气/文体（如：青春校园、冷峻第三人称）",
  "style_guide": "给译者的风格指南（中文，3-6 条要点）",
  "narration": "叙事人称与时态（如：第一人称限知、过去时）",
  "pacing": "句式节奏（长短句比例、断句习惯、段落密度）",
  "register": "语域（书面/口语/文白程度）",
  "dialogue_style": "对话风格（口癖、语气词、称呼习惯）",
  "rhetoric": "修辞倾向（比喻密度、心理描写方式等）",
  "characters": [{"entity_id":"稳定实体ID(可空)","source":"原文主要写法","aliases":[{"source":"别名/称谓","visible_from_chapter":0,"visible_from_segment":0,"visible_until_chapter":null,"visible_until_segment":null,"status":"confirmed/suspected","evidence":"确认同一实体的原文证据"}],"reading":"读音(可空)","target":"建议中文译名","gender":"男/女/未知","gender_confidence":"confirmed/suspected/unknown","gender_evidence":"原文中的直接证据；没有则留空","gender_evidence_chapter":"证据所在0基章节号；无法定位则为null","gender_evidence_segment":"证据所在0基段号；无法定位则为null","voice":"不含剧情事实的说话方式：自称、口癖、敬语习惯","note":"人物关系或歧义，仅供分析存档，不直接注入早期翻译"}],
  "terms": [{"source":"原文词","reading":"读音(可空)","target":"建议中文译法","type":"地名/组织/术语","note":""}]
}\
""")

ANALYZER_USER = Template("""\
【样章原文（$src_label）】
$sample

请分析并输出上述 JSON。人名、地名、专有名词尽量找全，译名力求自然且符合中文小说习惯。
性别只有在原文明确说明（如男/女、父/母、兄/姐等明确身份）时标 confirmed；
人名印象、外貌、服装、语气以及 私/僕/俺/あたし 等第一人称只能标 suspected，不能作为确定事实。
confirmed 必须同时给出可核对的 gender_evidence 以及证据所在的0基章节、段号；不能定位时必须降为 unknown/suspected。
别名与身份关联也必须记录从哪一章哪一段起才对叙事可见；后文才揭示的关联不得从第0章生效。
若人物可能伪装、被误认或后文反转，保持 unknown/suspected，并在 note 中记录歧义，不得用后文答案覆盖早期叙事视角。
样章可能取自全书开头/中部/结尾（见标注），请综合判断整体风格及其演变。\
""")

GLOSSARY_EXTRACTOR_SYSTEM = Template("""\
你是小说翻译项目的术语与称呼抽取器。从给定的$src_label原文与其中文译文中，抽取应进入全书对照表的稳定实体。
必须抽取：
1. 专有实体：人名、地名、组织名、作品内专有术语、招式名、物品名、设定名。
2. aliases 只允许不涉及身份推断的透明写法变化（空格/全半角、姓名直接加 さん/ちゃん/君/様/先輩/先生）。
   昵称、外号、假名、代号、职务与本名是否为同一人的判断属于带时间的叙事事实，不得在这里建立永久 alias；需要独立译法时输出独立词条。
不要把仅增加「さん/ちゃん/先輩」等敬称后缀的写法另建人物；已有实体或 aliases 覆盖时不要重复输出。
不要抽取纯敬称、口癖、固定表达、普通寒暄、一次性句子或常见词汇；这些属于局部语境，不是全书专有名词。
抽取原则：
- 依据本批译文中实际采用的中文写法填写 target，不要凭空创造译名。
- 若同一 source 在已有对照表中已有译法，尽量沿用；若本批译文出现明显不同译法，也照实输出，交由系统记录冲突。
- 对照表可能包含本批未出现条目，不要重复输出未在本批原文或译文中得到确认的项。
术语字段说明：$term_guidance
仅输出 JSON：
{"terms":[{"source":"原文专有名词或独立译法称谓","reading":"读音(可空)","target":"本批译文中实际采用的中文译法","type":"人物/地名/组织/术语/招式/称谓","gender":"男/女/未知(仅人物)","aliases":["同一实体的其它原文写法/昵称/简称"],"note":"归属、身份或统一理由"}]}\
""")

GLOSSARY_EXTRACTOR_USER = Template("""\
【已有对照表（参考，尽量沿用其译法）】
$glossary

【原文（$src_label）】
$source

【译文（中文）】
$target

请只抽取新出现或被本批确认的稳定专有名词，输出 JSON：{"terms":[...]}。\
""")

BACKTRANSLATE_SYSTEM = Template("""\
你是回译译者。把给定的中文译文回译成$src_label，只看中文、忠实表达其含义，输出 JSON：
{"backtranslations":["...",...]}，长度与输入一致。\
""")

BACKTRANSLATE_USER = Template("""\
【中文译文】（共 $n 段）
$numbered_target

输出 JSON：{"backtranslations":[...]}。\
""")

CONSISTENCY_SYSTEM = Template("""\
你是全书一致性审查员。给定专有名词对照表和若干章节译文摘要，检查：
术语译法是否前后统一、同一人物代词性别是否一致、语气文体是否漂移、标点是否统一为简体中文规范。
仅输出 JSON：{"issues":[{"type":"terminology/pronoun/tone/punctuation","detail":"...","where":"章节线索"}]}。\
""")

CHAPTER_DIGEST_SYSTEM = Template("""\
你是小说章节梗概员。阅读给定的$src_label单章原文，用简体中文写出该章梗概（不超过 200 字）：
交代本章关键情节推进、登场人物及其处境、重要信息或转折，去除细枝末节。只输出梗概正文，不要解释。\
""")

CHAPTER_DIGEST_USER = Template("""\
【章节原文（$src_label）】
$source

请输出该章中文梗概（不超过 200 字）。\
""")

BOOK_SYNOPSIS_SYSTEM = Template("""\
你是小说全书概览员。依据【前期分析】与【各章梗概】，用简体中文写出一份"全书概览"（不超过 500 字），
供译者在翻译任意章节前把握全局，避免与后文冲突：
主线剧情走向与结局、主要人物及其关系与弧光、核心设定/谜底/重要伏笔、整体基调。
只输出概览正文，不要解释或分点编号。\
""")

BOOK_SYNOPSIS_USER = Template("""\
【前期分析】
$analysis

【各章梗概】
$digests

请综合以上，输出全书概览（不超过 500 字）。\
""")

_DEFAULTS = {
    "translator_system": TRANSLATOR_SYSTEM,
    "translator_user": TRANSLATOR_USER,
    "translator_fix_user": TRANSLATOR_FIX_USER,
    "reviewer_system": REVIEWER_SYSTEM,
    "reviewer_user": REVIEWER_USER,
    "glossary_arbiter_system": GLOSSARY_ARBITER_SYSTEM,
    "glossary_arbiter_user": GLOSSARY_ARBITER_USER,
    "polisher_system": POLISHER_SYSTEM,
    "polisher_user": POLISHER_USER,
    "title_translator_system": TITLE_TRANSLATOR_SYSTEM,
    "title_translator_user": TITLE_TRANSLATOR_USER,
    "analyzer_system": ANALYZER_SYSTEM,
    "analyzer_user": ANALYZER_USER,
    "glossary_extractor_system": GLOSSARY_EXTRACTOR_SYSTEM,
    "glossary_extractor_user": GLOSSARY_EXTRACTOR_USER,
    "backtranslate_system": BACKTRANSLATE_SYSTEM,
    "backtranslate_user": BACKTRANSLATE_USER,
    "consistency_system": CONSISTENCY_SYSTEM,
    "chapter_digest_system": CHAPTER_DIGEST_SYSTEM,
    "chapter_digest_user": CHAPTER_DIGEST_USER,
    "book_synopsis_system": BOOK_SYNOPSIS_SYSTEM,
    "book_synopsis_user": BOOK_SYNOPSIS_USER,
}

def render(
    name: str,
    *,
    src: str = "ja",
    tgt: str = "zh",
    quote_style: str = "source",
    **kwargs,
) -> str:
    """渲染内置模板；按 src 自动注入语言相关默认占位。"""
    tmpl = _DEFAULTS[name]
    # 语言相关默认值（调用方可用同名 kwarg 覆盖）
    kwargs.setdefault("src_label", langprofile.label(src))
    kwargs.setdefault("tgt_label", langprofile.label(tgt))
    kwargs.setdefault("lang_guidance", langprofile.translate_guidance(src))
    kwargs.setdefault("term_guidance", langprofile.term_guidance(src))
    kwargs.setdefault("punct_rule", punctuation_rule(quote_style))
    return tmpl.safe_substitute(**kwargs)


# ── 渲染辅助 ───────────────────────────────────────────────────────────────
def honorific_rule(strategy: str) -> str:
    """敬称规则（保留以兼容调用方）；底层委托 langprofile。"""
    return langprofile.honorific_rule(strategy)


def render_glossary(terms: list[GlossaryTerm]) -> str:
    """把术语对象渲染为适合注入模型提示词的逐行对照表。"""
    if not terms:
        return "（暂无）"
    lines = []
    for t in terms:
        extra = []
        if t.gender and t.status in {"verified", "confirmed"}:
            extra.append(t.gender)
        if t.reading:
            extra.append(f"读音:{t.reading}")
        tag = f"（{t.type}{('，' + '，'.join(extra)) if extra else ''}）"
        alias = f" [别名: {', '.join(t.aliases)}]" if t.aliases else ""
        lines.append(f"- {t.source} → {t.target}{tag}{alias}")
    return "\n".join(lines)


def numbered(texts: list[str]) -> str:
    """把文本列表渲染成以零为起点的方括号编号格式。"""
    return "\n".join(f"[{i}] {t}" for i, t in enumerate(texts))


def numbered_pairs(sources: list[str], targets: list[str]) -> str:
    """按相同下标并排渲染原文和译文，供审校提示词使用。"""
    out = []
    for i, (s, t) in enumerate(zip(sources, targets)):
        out.append(f"[{i}] 原文：{s}\n    译文：{t}")
    return "\n".join(out)
