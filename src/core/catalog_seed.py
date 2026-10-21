# -*- coding: utf-8 -*-
"""内置常用中药饮片 / 成药目录（一键载入用）。

背景：新装的库里没有任何药品，医生开方时搜"当归"搜不到，
只能先去"目录维护"里一味味手工建档——这是"开方太麻烦"的第一道坎。
本模块提供一批常用饮片（含拼音首字母、常用量、参考价），
由 /api/catalog/seed 幂等写入：**按正名去重，已存在的跳过，绝不覆盖**。

字段：name | pinyin | initials | price_yuan_per_g | usual_dose_g | flag
flag: T=毒性饮片（超量强制二次确认，F113）  P=妊娠慎用/禁忌
价格是市场参考价（元/克），仅作初始值，请按实际进货价在开方页修改。
"""

# name | pinyin | initials | 元/克 | 常用量(g) | flag
_HERB_ROWS = """
黄芪|huangqi|hq|0.05|15|
炙黄芪|zhihuangqi|zhq|0.06|15|
党参|dangshen|ds|0.09|15|
太子参|taizishen|tzs|0.12|15|
西洋参|xiyangshen|xys|0.35|6|
人参|renshen|rs|0.60|9|
白术|baizhu|bz|0.06|12|
炒白术|chaobaizhu|cbz|0.07|12|
茯苓|fuling|fl|0.05|15|
茯神|fushen|fs|0.06|15|
甘草|gancao|gc|0.04|6|
炙甘草|zhigancao|zgc|0.06|6|
当归|danggui|dg|0.09|12|
白芍|baishao|bs|0.05|12|
赤芍|chishao|cs|0.05|12|
川芎|chuanxiong|cx|0.06|10|
熟地黄|shudihuang|sdh|0.06|15|
生地黄|shengdihuang|sdih|0.04|15|
阿胶|ejiao|ej|0.35|9|
枸杞子|gouqizi|gqz|0.07|12|
山药|shanyao|sy|0.04|15|
山茱萸|shanzhuyu|szy|0.08|12|
大枣|dazao|dz|0.02|15|
龙眼肉|longyanrou|lyr|0.05|12|
制何首乌|zhiheshouwu|zhsw|0.04|12|
女贞子|nvzhenzi|nzz|0.02|12|
墨旱莲|mohanlian|mhl|0.02|12|
麦冬|maidong|md|0.09|12|
天冬|tiandong|td|0.06|12|
北沙参|beishashen|bss|0.06|12|
南沙参|nanshashen|nss|0.05|12|
玉竹|yuzhu|yz|0.04|12|
淡竹叶|danzhuye|dzy|0.02|10|
芦根|lugen|lg|0.02|15|
白扁豆|baibiandou|bbd|0.03|12|
百合|baihe|bh|0.05|12|
石斛|shihu|sh|0.20|12|
黄精|huangjing|hj|0.06|15|
灵芝|lingzhi|lz|0.08|12|
杜仲|duzhong|dzh|0.04|12|
续断|xuduan|xd|0.04|12|
补骨脂|buguzhi|bgz|0.03|10|
益智仁|yizhiren|yzr|0.06|10|
菟丝子|tusizi|tsz|0.05|12|
巴戟天|bajitian|bjt|0.12|10|
淫羊藿|yinyanghuo|yyh|0.05|10|
仙茅|xianmao|xm|0.06|10|
肉苁蓉|roucongrong|rcr|0.12|12|
锁阳|suoyang|syang|0.06|10|
麻黄|mahuang|mh|0.03|6|
桂枝|guizhi|gz|0.02|10|
荆芥|jingjie|jj|0.02|10|
防风|fangfeng|ff|0.05|10|
紫苏叶|zisuye|zsy|0.02|10|
生姜|shengjiang|sj|0.01|10|
薄荷|bohe|bhe|0.02|6|
桑叶|sangye|sye|0.02|10|
菊花|juhua|jh|0.05|10|
柴胡|chaihu|ch|0.12|10|
葛根|gegen|gg|0.02|15|
升麻|shengma|sm|0.03|6|
蝉蜕|chantui|ct|0.30|6|
牛蒡子|niubangzi|nbz|0.03|10|
蔓荆子|manjingzi|mjz|0.03|10|
辛夷|xinyi|xy|0.08|6|
苍耳子|cangerzi|cez|0.02|10|
白芷|baizhi|bzhi|0.03|10|
细辛|xixin|xx|0.15|3|T
羌活|qianghuo|qh|0.10|10|
独活|duhuo|dh|0.04|10|
藁本|gaoben|gb|0.05|10|
石膏|shigao|sg|0.01|30|
知母|zhimu|zm|0.03|12|
栀子|zhizi|zz|0.03|10|
夏枯草|xiakucao|xkc|0.03|15|
决明子|juemingzi|jmz|0.02|15|
黄芩|huangqin|hqin|0.04|10|
黄连|huanglian|hl|0.25|6|
黄柏|huangbai|hb|0.04|10|
龙胆草|longdancao|ldc|0.05|6|
苦参|kushen|ks|0.03|10|
金银花|jinyinhua|jyh|0.12|15|
连翘|lianqiao|lq|0.08|12|
板蓝根|banlangen|blg|0.03|15|
大青叶|daqingye|dqy|0.02|15|
蒲公英|pugongying|pgy|0.02|15|
紫花地丁|zihuadiding|zhdd|0.02|15|
鱼腥草|yuxingcao|yxc|0.02|15|
败酱草|baijiangcao|bjc|0.02|15|
白鲜皮|baixianpi|bxp|0.05|10|
玄参|xuanshen|xs|0.04|12|
牡丹皮|mudanpi|mdp|0.06|10|
紫草|zicao|zc|0.08|10|
青蒿|qinghao|qhao|0.02|10|
地骨皮|digupi|dgp|0.04|12|
银柴胡|yinchaihu|ych|0.15|10|
大黄|dahuang|dhg|0.03|10|
芒硝|mangxiao|mx|0.01|10|
番泻叶|fanxieye|fxy|0.03|6|
火麻仁|huomaren|hmr|0.02|15|
郁李仁|yuliren|ylr|0.03|10|
威灵仙|weilingxian|wlx|0.03|10|
防己|fangji|fj|0.05|10|
秦艽|qinjiao|qj|0.08|10|
桑寄生|sangjisheng|sjs|0.02|15|
五加皮|wujiapi|wjp|0.03|10|
木瓜|mugua|mg|0.03|12|
豨莶草|xixiancao|xxc|0.02|15|
桑枝|sangzhi|szhi|0.01|15|
伸筋草|shenjincao|sjc|0.02|15|
海风藤|haifengteng|hft|0.03|15|
苍术|cangzhu|cz|0.06|10|
厚朴|houpo|hp|0.04|10|
广藿香|guanghuoxiang|ghx|0.03|10|
佩兰|peilan|pl|0.02|10|
砂仁|sharen|sr|0.25|6|
豆蔻|doukou|dk|0.15|6|
草果|caoguo|cg|0.05|6|
石菖蒲|shichangpu|scp|0.05|10|
猪苓|zhuling|zl|0.08|12|
泽泻|zexie|zx|0.03|12|
薏苡仁|yiyiren|yyr|0.02|20|
车前子|cheqianzi|cqz|0.03|12|
滑石|huashi|hs|0.01|15|
通草|tongcao|tc|0.08|6|
金钱草|jinqiancao|jqc|0.02|20|
茵陈|yinchen|yc|0.02|15|
萆薢|bixie|bxie|0.03|12|
玉米须|yumixu|ymx|0.01|20|
附子|fuzi|fz|0.06|10|TP
干姜|ganjiang|gj|0.03|10|
肉桂|rougui|rg|0.05|5|
吴茱萸|wuzhuyu|wzy|0.10|5|
小茴香|xiaohuixiang|xhx|0.02|10|
丁香|dingxiang|dx|0.05|3|
高良姜|gaoliangjiang|glj|0.03|10|
花椒|huajiao|hjiao|0.04|6|
陈皮|chenpi|cp|0.02|10|
青皮|qingpi|qp|0.03|10|
枳实|zhishi|zs|0.03|10|
枳壳|zhiqiao|zq|0.02|10|
木香|muxiang|mx|0.03|10|
香附|xiangfu|xf|0.02|10|
乌药|wuyao|wy|0.02|10|
薤白|xiebai|xb|0.04|10|
佛手|foshou|fs|0.04|10|
玫瑰花|meiguihua|mgh|0.06|6|
川楝子|chuanlianzi|clz|0.02|10|
山楂|shanzha|szh|0.02|12|
六神曲|liushenqu|lsq|0.02|12|
麦芽|maiya|my|0.01|15|
莱菔子|laifuzi|lfz|0.02|10|
鸡内金|jineijin|jnj|0.06|10|
三七|sanqi|sq|0.60|6|
白及|baiji|bj|0.15|10|
仙鹤草|xianhecao|xhc|0.02|15|
地榆|diyu|dy|0.03|12|
槐花|huaihua|hhua|0.03|10|
侧柏叶|cebaiye|cby|0.02|10|
白茅根|baimaogen|bmg|0.02|15|
茜草|qiancao|qc|0.04|10|
艾叶|aiye|ay|0.02|10|P
丹参|danshen|dsh|0.05|15|
红花|honghua|hh|0.12|10|P
桃仁|taoren|tr|0.06|10|P
益母草|yimucao|ymc|0.02|15|P
牛膝|niuxi|nx|0.04|12|P
鸡血藤|jixueteng|jxt|0.02|15|
王不留行|wangbuliuxing|wblx|0.02|10|
延胡索|yanhusuo|yhs|0.10|10|
郁金|yujin|yj|0.05|10|
姜黄|jianghuang|jhg|0.03|10|
乳香|ruxiang|rx|0.08|6|
没药|moyao|myao|0.06|6|
三棱|sanleng|sl|0.03|10|P
莪术|ezhu|ez|0.03|10|P
水蛭|shuizhi|szhi2|0.60|3|TP
土鳖虫|tubiechong|tbc|0.10|6|TP
路路通|lulutong|llt|0.02|10|
法半夏|fabanxia|fbx|0.08|10|TP
姜半夏|jiangbanxia|jbx|0.08|10|TP
天南星|tiannanxing|tnx|0.08|6|TP
白附子|baifuzi|bfz|0.05|6|TP
白芥子|baijiezi|bjz|0.02|10|
桔梗|jiegeng|jg|0.05|10|
旋覆花|xuanfuhua|xfh|0.03|10|
前胡|qianhu|qh|0.05|10|
瓜蒌|gualou|gl|0.03|15|
浙贝母|zhebeimu|zbm|0.15|10|
川贝母|chuanbeimu|cbm|1.20|6|
竹茹|zhuru|zr|0.02|10|
苦杏仁|kuxingren|kxr|0.04|10|
紫苏子|zisuzi|zsz|0.02|10|
百部|baibu|bb|0.03|10|
紫菀|ziwan|zw|0.04|10|
款冬花|kuandonghua|kdh|0.10|10|
枇杷叶|pipaye|ppy|0.02|12|
桑白皮|sangbaipi|sbp|0.02|12|
葶苈子|tinglizi|tlz|0.03|10|
白果|baiguo|bg|0.04|10|
磁石|cishi|cs|0.02|30|
龙骨|longgu|lg|0.05|30|
牡蛎|muli|ml|0.02|30|
酸枣仁|suanzaoren|szr|0.25|15|
柏子仁|baiziren|bzr|0.10|12|
远志|yuanzhi|yzhi|0.10|10|
合欢皮|hehuanpi|hhp|0.02|15|
首乌藤|shouwuteng|swt|0.02|15|
珍珠母|zhenzhumu|zzm|0.02|30|
石决明|shijueming|sjm|0.03|30|
代赭石|daizheshi|dzs|0.02|30|
钩藤|gouteng|gt|0.06|12|
天麻|tianma|tm|0.20|10|
地龙|dilong|dl|0.05|10|
全蝎|quanxie|qx|0.80|5|TP
蜈蚣|wugong|wg|0.60|3|TP
僵蚕|jiangcan|jc|0.10|10|
刺蒺藜|cijili|cjl|0.03|10|
五味子|wuweizi|wwz|0.12|6|
乌梅|wumei|wm|0.04|10|
麻黄根|mahuanggen|mhg|0.03|10|
浮小麦|fuxiaomai|fxm|0.01|20|
覆盆子|fupenzi|fpz|0.10|10|
桑螵蛸|sangpiaoxiao|spx|0.15|10|
金樱子|jinyingzi|jyz|0.04|12|
莲子|lianzi|lz|0.04|15|
芡实|qianshi|qs|0.04|15|
诃子|hezi|hz|0.03|6|
肉豆蔻|roudoukou|rdk|0.06|6|
槟榔|binglang|bl|0.03|10|
使君子|shijunzi|sjz|0.04|10|
"""

# name | pinyin | initials | 单价(元) | 单位 | 规格
_DRUG_ROWS = """
感冒灵颗粒|ganmaolingkeli|gmlkl|12.80|盒|9袋/盒|
板蓝根颗粒|banlangenkeli|blgkl|9.80|盒|10袋/盒|
小柴胡颗粒|xiaochaihukeli|xchkl|15.60|盒|10袋/盒|
藿香正气水|huoxiangzhengqishui|hxzqs|8.50|盒|10支/盒|
银翘解毒片|yinqiaojiedupian|yqjdp|10.20|盒|24片/盒|
连花清瘟胶囊|lianhuaqingwenjiaonang|lhqwjn|19.80|盒|24粒/盒|
布洛芬缓释胶囊|buluofenghuanshijiaonang|blfhsjn|16.50|盒|20粒/盒|
对乙酰氨基酚片|duiyixiananjifenpian|dyxajfp|8.90|盒|24片/盒|
阿莫西林胶囊|amoxilinjiaonang|amxljn|13.50|盒|24粒/盒|
头孢克肟分散片|toubaokewofensanpian|tbkwfsp|28.60|盒|12片/盒|
阿奇霉素片|aqimeisupian|aqmsp|22.40|盒|6片/盒|
左氧氟沙星片|zuoyangfushaxingpian|zyfsxp|18.90|盒|12片/盒|
奥美拉唑肠溶胶囊|aomeilazuochangrongjiaonang|amlzcjn|26.80|盒|14粒/盒|
蒙脱石散|mengtuoshisan|mtss|15.90|盒|12袋/盒|
多潘立酮片|duopantongpian|dptp|12.60|盒|24片/盒|
氨溴索口服液|anxiusuokoufuye|axskfy|18.50|瓶|100ml/瓶|
复方甘草片|fufanggancaopian|ffgcp|9.20|盒|24片/盒|
硝苯地平缓释片|xiaobendipinghuanshipian|xbdphsp|23.70|盒|24片/盒|
二甲双胍缓释片|erjiashuangguahuanshipian|ejsgphsp|19.40|盒|30片/盒|
格列美脲片|geliemeiniaopian|glmnp|28.90|盒|24片/盒|
阿卡波糖片|akabotangpian|akbtp|36.50|盒|30片/盒|
阿托伐他汀钙片|atuofatatinggaipian|atfttgp|42.80|盒|7片/盒|
阿司匹林肠溶片|asipilinchangrongpian|asplcrp|11.60|盒|30片/盒|
维生素C片|weishengsu Cpian|wsscp|6.80|瓶|100片/瓶|
维生素B1片|weishengsu B1pian|wssb1p|5.60|瓶|100片/瓶|
碳酸钙D3片|tansuangai D3pian|tsgd3p|38.90|瓶|60片/瓶|
云南白药膏|yunnanbaiyaogao|ynbyg|22.50|盒|5贴/盒|
碘伏消毒液|dianfuxiaoduye|dfxdy|9.80|瓶|100ml/瓶|
医用酒精|yiyoungjiujing|yyjj|7.50|瓶|500ml/瓶|
一次性使用注射器|yicixingshiyongzhusheqi|ycxsyzsq|1.20|支|5ml/支|
"""


def _parse(rows, ncol):
    out = []
    for line in rows.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < ncol:
            continue
        out.append([p.strip() for p in parts[:ncol]])
    return out


def herbs():
    """[(name, pinyin, initials, price_yuan_per_g, usual_dose_g, flag)]"""
    out = []
    for r in _parse(_HERB_ROWS, 6):
        try:
            out.append((r[0], r[1], r[2], float(r[3]), float(r[4]), r[5]))
        except ValueError:
            continue
    return out


def drugs():
    """[(name, pinyin, initials, price_yuan, unit, spec)]"""
    out = []
    for r in _parse(_DRUG_ROWS, 6):
        try:
            out.append((r[0], r[1], r[2], float(r[3]), r[4], r[5]))
        except ValueError:
            continue
    return out


if __name__ == "__main__":
    print("herbs=%d drugs=%d" % (len(herbs()), len(drugs())))
    for h in herbs()[:5]:
        print(h)
