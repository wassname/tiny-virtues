"""Evaluation primers and source-tradition lookup. -- PI/OpenAI"""
PRIMERS = dict(stoic='stoic', confucian='confucian', taoist='taoist', buddhist='theravada_narrative', christian='gospels')


# Which school's source each case-id prefix comes from (None: 'other', no school primer: Lüshi chunqiu, Aesop, Goblins). -- PI/Claude
SOURCE_SCHOOL = dict(
    gos='christian', des='christian', cas='christian', grg='christian', gld='christian', cel='christian', mrg='christian',
    sev='christian', bed='christian', eph='christian', ado='christian',
    ana='confucian', men='confucian', mnc='confucian', lk='confucian', zzh='confucian', fil='confucian', twc='confucian',
    xun='confucian', lnz='confucian',
    zhu='taoist', zhw='taoist', lie='taoist', lzg='taoist', hnz='taoist', hnm='taoist', wnz='taoist', lsc=None, xuh='confucian', sxz='taoist', kyp='taoist', ycw='taoist',
    mn='buddhist', jtk='buddhist', bl='buddhist', vin='buddhist', ud='buddhist', mil='buddhist', thc='buddhist', tic='buddhist',
    mjc='buddhist', big='buddhist', dig='buddhist', hdy='buddhist', mhv='buddhist', ksy='buddhist',
    jat='buddhist', aes=None, gob=None)
