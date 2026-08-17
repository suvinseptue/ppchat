#!/usr/bin/env python3
"""One-off: persist the agent-authored summary + requirements for 富德系统支持 2026-08-17.

This is the Approach-A deliverable: the reasoning/content below was produced by
the agent from out/富德系统支持/2026-08-17/messages.json; this script just writes it
through the app-layer writers (which also render summary.md).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppchat import app, store

GROUP = "富德系统支持"
DATE = "2026-08-17"


def images_for(con, message_ids, note):
    out = []
    for mid in message_ids:
        for im in store.get_images(con, mid):
            out.append({"message_id": mid, "src_ref": im["src_ref"],
                        "local_path": im["local_path"], "note": note})
    return out


def main():
    con = store.connect()

    summary = {
        "group": GROUP,
        "date": DATE,
        "overview": "富德货柜团队在系统支持群反馈 ERP 使用中的多个问题，集中在放箱单/发票号编号与校验、"
                    "销售放箱单与采购单的同步（我方代出）、堆场工作时间字段缺失，以及放箱单列表柜号显示不全等；"
                    "Suvin 逐条回应并给出规则说明或承诺优化。",
        "topics": [
            {"title": "自定义放箱单号 / 发票号格式",
             "detail": "Mina 希望系统能记录并按客户自有格式生成放箱单号（类似发票号有固定格式）；"
                       "Suvin 请其提供格式样例，将据此生成编号，并确认现有发票号是否也需按新格式。",
             "participants": ["富德货柜-Mina 集装箱租售", "Suvin"],
             "message_ids": [435, 436, 438, 439, 440, 441, 442, 443]},
            {"title": "业务模块排序调整",
             "detail": "Amber 要求把“业务”模块移动到“采购发票”后面。",
             "participants": ["Amber-Forward container富德货柜"],
             "message_ids": [444, 445]},
            {"title": "销售放箱单与采购单同步（我方代出）",
             "detail": "Mina 在销售侧创建放箱单、选择对应采购柜号并确认提走后，采购侧看不到放箱单信息。"
                       "Suvin 说明销售/采购放箱单是不同字段，仅勾选“我方代出”才会同步；Mina 称已勾选且选了"
                       "对应柜号仍未同步，双方结合多张截图排查中。",
             "participants": ["富德货柜-Mina 集装箱租售", "Suvin"],
             "message_ids": [446, 447, 448, 449, 452, 453, 455, 457, 458, 459, 460, 461, 462, 464, 465, 466]},
            {"title": "堆场工作时间字段缺失",
             "detail": "堆场没有记录“工作时间”的字段，只能写备注，导致导出的放箱单缺少工作时间。"
                       "Suvin 确认是否所有放箱单都要展示堆场工作时间。",
             "participants": ["富德货柜-Mina 集装箱租售", "Suvin"],
             "message_ids": [450, 451, 456, 459]},
            {"title": "放箱单列表柜号显示不全",
             "detail": "Mina 反馈列表里柜号显示不全，Suvin 判断是展示字段过多，承诺优化。",
             "participants": ["富德货柜-Mina 集装箱租售", "Suvin"],
             "message_ids": [467, 468, 469]},
            {"title": "发票号重复校验规则",
             "detail": "Lyra 询问发票号是否不能重复；Suvin 说明：发票号若已被“已提交”的采购单占用则不可再用，"
                       "但若另一采购单仍是草稿状态则仍可使用。",
             "participants": ["富德-Lyra", "Suvin"],
             "message_ids": [470, 471, 472, 473, 474]},
        ],
        "participants": [
            {"name": "富德货柜-Mina 集装箱租售", "message_count": 18},
            {"name": "Suvin", "message_count": 15},
            {"name": "Amber-Forward container富德货柜", "message_count": 4},
            {"name": "富德-Lyra", "message_count": 3},
        ],
        "todos": [
            {"text": "提供放箱单号/发票号的格式样例，供系统按格式生成编号",
             "owner": "富德货柜-Mina 集装箱租售", "message_ids": [440, 441, 442, 443]},
            {"text": "优化放箱单列表展示字段，解决柜号显示不全",
             "owner": "Suvin", "message_ids": [469]},
            {"text": "排查“我方代出”放箱单未同步到采购单的问题",
             "owner": "Suvin", "message_ids": [458, 465, 466]},
            {"text": "评估并调整“业务”模块到“采购发票”之后",
             "owner": "Suvin", "message_ids": [444]},
        ],
    }

    reqs = {
        "group": GROUP,
        "date": DATE,
        "requirements": [
            {"id": "R1", "title": "支持自定义放箱单号/发票号格式",
             "detail": "客户有自己的放箱单号编码格式（类似发票号），希望系统能记录并按其格式生成放箱单号；"
                       "现有发票号可能也需符合该格式。待客户提供格式样例。",
             "raised_by": "富德货柜-Mina 集装箱租售", "status": "open",
             "message_ids": [436, 439, 440, 441, 442],
             "images": images_for(con, [435], "Mina 展示放箱单页面，指出无处填写自编放箱单号")},
            {"id": "R2", "title": "调整“业务”模块排序到“采购发票”之后",
             "detail": "Amber 要求把“业务”菜单/模块移动到“采购发票”后面。",
             "raised_by": "Amber-Forward container富德货柜", "status": "open",
             "message_ids": [444],
             "images": images_for(con, [445], "Amber 附截图说明模块位置")},
            {"id": "R3", "title": "销售放箱单（我方代出）应同步到对应采购单",
             "detail": "勾选“我方代出”并选择对应采购柜号后，放箱单信息未同步到采购侧；用户期望销售与其对应采购"
                       "共用同一放箱单，避免两边重复录入。需排查同步逻辑或明确交互。",
             "raised_by": "富德货柜-Mina 集装箱租售", "status": "investigating",
             "message_ids": [447, 453, 455, 457, 458],
             "images": images_for(con, [446, 448, 449, 460, 464, 466],
                                   "销售/采购放箱单同步排查相关截图")},
            {"id": "R4", "title": "堆场增加“工作时间”字段并在放箱单展示/导出",
             "detail": "堆场缺少记录“工作时间”的字段，只能写在备注，导致导出的放箱单没有工作时间。",
             "raised_by": "富德货柜-Mina 集装箱租售", "status": "open",
             "message_ids": [450, 459],
             "images": images_for(con, [451], "Mina 展示堆场页面，缺工作时间字段")},
            {"id": "R5", "title": "优化放箱单列表：柜号显示不全",
             "detail": "放箱单列表展示字段过多，导致柜号显示不全，需优化列表字段/布局。",
             "raised_by": "富德货柜-Mina 集装箱租售", "status": "accepted",
             "message_ids": [467, 469],
             "images": images_for(con, [468], "Mina 展示柜号显示不全的列表")},
            {"id": "R6", "title": "明确发票号重复校验规则", 
             "detail": "发票号唯一性规则：被“已提交”采购单占用的发票号不可复用；若另一采购单为草稿状态则仍可用。"
                       "此为规则澄清，Suvin 已答复。",
             "raised_by": "富德-Lyra", "status": "answered",
             "message_ids": [470, 472, 473],
             "images": images_for(con, [471], "Lyra 展示发票号提示")},
        ],
    }

    con.close()
    p1 = app.save_summary(GROUP, DATE, summary)
    p2 = app.save_requirements(GROUP, DATE, reqs)
    print("wrote:", p1)
    print("wrote:", app.bundle_dir(GROUP, DATE) / "summary.md")
    print("wrote:", p2)
    print(f"topics={len(summary['topics'])} todos={len(summary['todos'])} "
          f"requirements={len(reqs['requirements'])}")


if __name__ == "__main__":
    main()
