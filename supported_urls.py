"""Shared validation for recorder-supported live URLs."""

from urllib.parse import urlsplit


SUPPORTED_HOSTS = frozenset({
    "live.douyin.com", "v.douyin.com", "www.douyin.com", "live.kuaishou.com",
    "www.huya.com", "www.douyu.com", "www.yy.com", "live.bilibili.com",
    "www.redelight.cn", "www.xiaohongshu.com", "xhslink.com", "www.bigo.tv",
    "slink.bigovideo.tv", "app.blued.cn", "cc.163.com", "qiandurebo.com",
    "fm.missevan.com", "look.163.com", "twitcasting.tv", "live.baidu.com",
    "weibo.com", "fanxing.kugou.com", "fanxing2.kugou.com", "mfanxing.kugou.com",
    "www.huajiao.com", "www.7u66.com", "wap.7u66.com", "live.acfun.cn",
    "m.acfun.cn", "live.tlclw.com", "wap.tlclw.com", "live.ybw1666.com",
    "wap.ybw1666.com", "www.inke.cn", "www.zhihu.com", "www.haixiutv.com",
    "h5webcdnp.vvxqiu.com", "17.live", "www.lang.live", "m.pp.weimipopo.com",
    "v.6.cn", "m.6.cn", "www.lehaitv.com", "h.catshow168.com", "e.tb.cn",
    "huodong.m.taobao.com", "3.cn", "eco.m.jd.com", "www.miguvideo.com",
    "m.miguvideo.com", "show.lailianjie.com", "www.imkktv.com", "www.picarto.tv",
    "www.tiktok.com", "play.sooplive.co.kr", "m.sooplive.co.kr", "www.sooplive.com",
    "m.sooplive.com", "www.pandalive.co.kr", "www.winktv.co.kr", "www.flextv.co.kr",
    "www.ttinglive.com", "www.popkontv.com", "www.twitch.tv", "www.liveme.com",
    "www.showroom-live.com", "chzzk.naver.com", "m.chzzk.naver.com",
    "www.youtube.com", "youtu.be", "www.faceit.com",
})


def is_supported_url(url: str) -> bool:
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    lowered_url = url.lower()
    return (
        host in SUPPORTED_HOSTS
        or "live.shopee." in host
        or ".shp.ee" in host
        or any(extension in lowered_url for extension in (".flv", ".m3u8"))
    )
