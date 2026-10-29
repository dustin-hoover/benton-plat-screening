import re,collections
NUM=r"(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)\s*(?:'|’|‘|°|`|_+|\"|”|FT\.?|FEET|FOOT|-FT|-FOOT)?\s*[-—]?\s*"
KINDS=[("BSB_UE",r"B\.?\s?S\.?\s?B\.?\s*(?:&|AND|8)\s*U\.?\s?E\.?"),
       ("UE",r"(?:U\.\s?E\.|U/E\b|UTILITY\s+(?:ESMT|EASEMENT|ESM'?T)|GENERAL\s+UTILITY\s+EASEMENT|UTIL\.?\s+ESMT)"),
       ("DE_UE",r"(?:D\.\s?E\.?\s*(?:&|AND)\s*U\.\s?E\.?|U\.\s?E\.?\s*(?:&|AND)\s*D\.\s?E\.?|UTILITY\s*(?:&|AND)\s*DRAINAGE)"),
       ("DE",r"(?:D\.\s?E\.|DRAINAGE\s+(?:EASEMENT|ESMT))"),
       ("ROW",r"(?:R/W|R\.O\.W\.?|RIGHT[\s-]*OF[\s-]*WAY)"),
       ("SEWER",r"(?:SEWER|SANITARY)\s+(?:EASEMENT|ESMT)"),("WATER",r"WATER\s*(?:LINE)?\s+(?:EASEMENT|ESMT)"),
       ("ACCESS",r"ACCESS\s+(?:EASEMENT|ESMT)"),("GAS",r"GAS\s+(?:LINE\s+)?(?:EASEMENT|ESMT)"),
       ("ELEC",r"(?:ELECTRIC|POWER)\s+(?:LINE\s+)?(?:EASEMENT|ESMT)"),("ESMT",r"(?:ESMT|EASEMENT)")]
def norm(t):
    t=t.upper().replace('\n',' ')
    t=re.sub(r'(\d)\s*[°`]\s*(?=[A-Z])',r"\1' ",t)
    t=re.sub(r'\s+',' ',t); return t
def callouts(text):
    t=norm(text); out=collections.Counter(); seen=set()
    for kind,pat in KINDS:
        for m in re.finditer(NUM+r"(?:WIDE\s+)?(?:"+pat+")",t):
            w=float(m.group(1))
            if not (2<=w<=150) or (w*2)%1: continue
            s=m.span()
            if any(a<=s[0]<b for a,b in seen): continue
            seen.add(s); out[(kind,w)]+=1
    return out
def ue_widths(c):
    ue=collections.Counter()
    for (k,w),n in c.items():
        if k in ('UE','BSB_UE','DE_UE','ESMT'): ue[w]+=n
    return ue
