"""记忆召回（词面 + 话题，不调模型）：换个说法也能找到，无关提问不带记忆。

两组固定案例：第一组调词表时用过；第二组是调好后另写的，用来看有没有只对着第一组调。
数字记在 PR 里，这里只钉住「不退步」：第一组全中，第二组至少 7/8，两组都不误带。
"""
import pytest

from src.memory.lexical import rank_memories

TUNED_MEMORIES = {
    "home": "用户住在杭州西湖区",
    "work": "用户在宁波的一家科技公司上班，做产品负责人",
    "wife": "用户的妻子叫小林，是小学老师",
    "cat": "家里养了一只橘猫，叫团子",
    "sleep": "用户一般晚上12点睡，早上7点半起床",
    "food": "用户对花生过敏，不吃香菜",
    "report": "汇报时先说结论，再说细节，越简短越好",
    "car": "用户开一辆特斯拉 Model 3",
    "phone": "用户的工作邮箱是公司域名的，不要发到私人邮箱",
    "travel": "出差偏好坐高铁，选靠窗座位",
    "coffee": "每天下午喝一杯美式咖啡，不加糖",
    "kid": "儿子今年上三年级，周三下午有游泳课",
}
TUNED_CASES = [  # (query, expected relevant ids; empty = nothing relevant should be injected)
    ("我住哪来着", {"home"}),
    ("帮我查一下我家附近周末有什么展览", {"home"}),
    ("我老婆是做什么的", {"wife"}),
    ("给我爱人挑个生日礼物", {"wife"}),
    ("我家猫叫什么名字", {"cat"}),
    ("宠物要打疫苗了提醒我", {"cat"}),
    ("我一般几点睡觉", {"sleep"}),
    ("明早几点叫我起来合适", {"sleep"}),
    ("推荐个晚饭吃什么", {"food"}),
    ("订个餐厅，注意我的忌口", {"food"}),
    ("下次汇报怎么写比较好", {"report"}),
    ("周末帮我规划去上海的行程，怎么去比较好", {"travel"}),
    ("我儿子哪天有课", {"kid"}),
    ("孩子周三几点放学", {"kid"}),
    ("我在哪家公司工作", {"work"}),
    ("车子该保养了", {"car"}),
    ("把这份文件发到我邮箱", {"phone"}),
    ("今天天气怎么样", set()),
    ("帮我写一首关于秋天的诗", set()),
    ("解释一下什么是量子计算", set()),
    ("这个问题你怎么看", set()),
    ("帮我翻译一下这段英文", set()),
]


HELD_MEMORIES = {"gf": "用户的女朋友叫小雨，在上海读研", "dog": "用户养了一条柯基，叫豆豆", "sport": "周末常去打羽毛球",
     "home": "用户住在上海浦东", "sea": "对海鲜过敏", "mom": "妈妈住在老家绍兴，每周日晚上视频",
     "job": "用户在一家外企做财务", "meet": "周一上午固定开部门例会"}
HELD_CASES = [("我女朋友生日快到了送什么", {"gf"}), ("豆豆该洗澡了", {"dog"}), ("这周末运动安排一下", {"sport"}),
     ("附近有什么好吃的海鲜馆", {"sea"}), ("提醒我给妈妈打视频", {"mom"}), ("我是做什么工作的", {"job"}),
     ("下周一上午有空吗", {"meet"}), ("帮我订个家附近的餐厅", {"home"}),
     ("今天股市怎么样", set()), ("写个Python脚本", set()), ("讲个笑话", set()), ("这段代码哪里有问题", set())]


def _ids(memories, query):
    rows = [{"id": key, "content": value} for key, value in memories.items()]
    return [row["id"] for row in rank_memories(rows, query, 3)]


@pytest.mark.parametrize("query,want", TUNED_CASES)
def test_tuned_cases(query, want):
    got = _ids(TUNED_MEMORIES, query)
    assert want <= set(got) if want else got == []


def test_held_out_cases_do_not_regress():
    hits = sum(1 for query, want in HELD_CASES if want and want <= set(_ids(HELD_MEMORIES, query)))
    assert hits >= 7
    assert all(_ids(HELD_MEMORIES, query) == [] for query, want in HELD_CASES if not want)


def test_function_word_bigrams_no_longer_cross_match():
    # 「保养了」曾经靠「养了」命中「养了一只猫」
    assert _ids({"cat": "家里养了一只橘猫"}, "空调该保养了") == []
