import time
import re
import unicodedata
import requests
import Levenshtein

""" Cleaning title and identifying author name """
def cleaning_title(title, typ):

    if typ == "oa":
        n = 6
    else:
        n = 4
    stoplist = [line.strip() for line in open("stopwords-it.txt")]
    stoplist = set(stoplist)
    keywords = [w for w in title.split(" ") if w not in stoplist]
    keywords = " ".join(keywords[:n])

    return keywords

def cleaning_name(name_raw):

    name_clean = u"".join([c for c in unicodedata.normalize("NFKD", name_raw) if not unicodedata.combining(c)])
    name_clean = name_clean.lower()
    name_clean = re.sub(r"[^\w\d\s]", "", name_clean)

    return name_clean


def identify_author(fullname, pub_authors):

    surname = cleaning_name(fullname[0].split(" ")[-1])
    name = cleaning_name(fullname[1].split(" ")[0])
    possible_ids = []

    for author in pub_authors:
        parts = author["AuN"].split(" ")
        for part in parts:
            if part == surname:
                possible_ids.append((author["AuId"], author["AuN"]))

    if len(possible_ids) == 1:
        return possible_ids[0][0]

    if len(possible_ids) > 1:
        for item in possible_ids:
            parts = item[1].split(" ")
            for part in parts:
                if part == name:
                    return item[0]


""" Searching in Microsoft Academic Graph """
def search_doi_mag(hdr_mag, fullname, doi):

    query = f"expr=DOI=='{doi}'&attributes=Id,AA.AuN,AA.AuId,Y,RId"
    url_mag = f"https://api.labs.cognitive.microsoft.com/academic/v1.0/evaluate?{query}"
    r = requests.get(url_mag, headers=hdr_mag).json()

    if "entities" in r.keys():
        if r["entities"]:
            auid = identify_author(fullname, r["entities"][0]["AA"])
            if auid:
                d = dict()
                d["PId"] = r["entities"][0]["Id"]
                if "RId" in r["entities"][0].keys():
                    d["RId"] = r["entities"][0]["RId"]

                return [d, auid]


def search_title_mag(hdr_mag, fullname, title, year):

    query = f"expr=And(Ti='{title}')&attributes=Id,DOI,AA.AuN,AA.AuId,Y,RId"
    url_mag = f"https://api.labs.cognitive.microsoft.com/academic/v1.0/evaluate?{query}"
    r = requests.get(url_mag, headers=hdr_mag).json()

    if "entities" in r.keys():
        for entity in r["entities"]:
            if year - 2 <= entity["Y"] <= year + 2:
                auid = identify_author(fullname, entity["AA"])
                if auid:
                    d = dict()
                    d["PId"] = entity["Id"]
                    if "DOI" in entity.keys():
                        d["doi"] = entity["DOI"]
                    if "RId" in entity.keys():
                        d["RId"] = entity["RId"]

                    return [d, auid]


""" Searching titles in OpenAIRE """
def search_title_oa(loggr, fullname, title, year):

    keywords = cleaning_title(title, "oa")
    surname = cleaning_name(fullname[0].split(" ")[-1])
    query = f"title={keywords}&author={surname}&toDateAccepted={str(year)}-12-31&page=1&size=5&format=json"
    url_oa = f"http://api.openaire.eu/search/publications?{query}"
    d = None
    r_oa = requests.get(url_oa)
    hdrs_oa = r_oa.headers

    try:
        r = r_oa.json()
        if r["response"]["results"] is not None:
            result = r["response"]["results"]["result"][0]["metadata"]["oaf:entity"]
            if "pid" in result["oaf:result"]:
                if type(result["oaf:result"]["pid"]) == list:
                    idx = 0
                    n = len(result["oaf:result"]["pid"])
                    while idx < n:
                        if result["oaf:result"]["pid"][idx]["@classid"] == "doi":
                            if "$" in result["oaf:result"]["pid"][idx].keys():
                                d = result["oaf:result"]["pid"][idx]["$"]
                                idx = n
                        idx += 1

                elif result["oaf:result"]["pid"]["@classid"] == "doi":
                    if "$" in result["oaf:result"]["pid"].keys():
                        d = result["oaf:result"]["pid"]["$"]

            else:
                d = None

            if "extraInfo" in result.keys() and result["extraInfo"]["@typology"] == "citations":
                c_raw = result["extraInfo"]["citations"]["citation"]
            else:
                c_raw = None

            return [d, c_raw]

    except Exception as ex:
        loggr.error("OA__" + repr(ex) + "__" + url_oa + "__" + hdrs_oa["Content-Type"])


""" Searching titles in CrossRef """
def search_title_cr(loggr, hdr_cr, fullname, title, year):

    keywords = cleaning_title(title, "cr")
    surname = cleaning_name(fullname[0].split(" ")[-1])
    name = cleaning_name(fullname[1].split(" ")[0])
    query = f"query.bibliographic={keywords}&query.author={surname}&rows=4&select=DOI,title,author,issued"
    url_cr = f"https://api.crossref.org/works?{query}"
    r_cr = requests.get(url_cr, headers=hdr_cr)
    hdrs_cr = r_cr.headers

    try:
        r = r_cr.json()
        possible = []
        if r["message"]["items"]:
            idx = 0
            while idx < len(r["message"]["items"]):
                point_a = 0
                point_b = 0
                if r["message"]["items"][idx]["issued"]["date-parts"][0][0]:
                    if r["message"]["items"][idx]["issued"]["date-parts"][0][0] == year:
                        point_a += 3
                    elif year - 1 < r["message"]["items"][idx]["issued"]["date-parts"][0][0] < year + 1:
                        point_a += 2
                    elif year - 2 < r["message"]["items"][idx]["issued"]["date-parts"][0][0] < year + 2:
                        point_a += 1

                for n in r["message"]["items"][idx]["author"]:
                    if n["family"].lower() == surname and n["given"].lower() == name:
                        point_b += 2
                    elif n["family"].lower() == surname and n["given"].lower()[0] == name[0]:
                        point_b += 1

                title_pub = r["message"]["items"][idx]["title"][0].lower()
                point_c = Levenshtein.ratio(title, title_pub)

                possible.append((point_c, point_b, point_a, idx))
                idx += 1

            sort = sorted(possible)
            if sort[-1][0] > 0.8 and sort[-1][1] >= 1 and sort[-1][2] >= 1:
                res = r["message"]["items"][sort[-1][3]]

                return res["DOI"]

    except Exception as ex:
        if hdrs_cr["content-type"] == 'text/plain' or hdrs_cr["content-type"] == 'text/html':
            r = r_cr.text
            if "503" in r:
                time.sleep(5.0)
                print(f"attempt:{url_cr}")
                solution = search_title_cr(loggr, hdr_cr, fullname, title, year)
                return solution
            else:
                loggr.error("CR__" + repr(ex) + "__" + url_cr + "__" + r)
        else:
            loggr.error("CR__" + repr(ex) + "__" + url_cr + "__" + hdrs_cr["content-type"])


def searching_ids(logger, authors_dict):

    lim_cr = 0
    hdr_mag = {'Ocp-Apim-Subscription-Key': 'ac0d6ea6f26845e8b41c0df9f4e45120'}
    hdr_cr = {'User-Agent': 'mailto:federica.bologna17@gmail.com'}

    for author, info in authors_dict.items():

        auids = set()
        for pub in info["pubbs"]:
            if "doi" in pub.keys():
                result = search_doi_mag(hdr_mag, info["fullname"], pub["doi"])
                if result is not None:
                    if result[0]:
                        pub.update(result[0])
                    if result[1]:
                        auids.add(result[1])

            elif "title" in pub.keys():
                result = search_title_mag(hdr_mag, info["fullname"], pub["title"], pub["year"])
                if result is not None:
                    if result[0]:
                        pub.update(result[0])
                    if result[1]:
                        auids.add(result[1])

                else:
                    result = search_title_oa(logger, info["fullname"], pub["title"], pub["year"])
                    if result is not None:
                        if result[0]:
                            pub["doi"] = result[0]
                        if result[1]:
                            pub["cited_raw"] = result[1]

                    else:
                        if lim_cr < 49:
                            result = search_title_cr(logger, hdr_cr, info["fullname"], pub["title"], pub["year"])
                            lim_cr += 1
                        else:
                            time.sleep(1.0)
                            result = search_title_cr(logger, hdr_cr, info["fullname"], pub["title"], pub["year"])
                            lim_cr = 1

                        if result is not None:
                            pub["doi"] = result

        info["AuIds"] = list(auids)

    return authors_dict


def adding_ids(logger, dd):

    print("adding ids")
    logger.error("________________SEARCH AUID________________")

    for asn_year, terms in dd["cand"].items():
        for term, roles in terms.items():
            for role, fields in roles.items():
                for field, candidates in fields.items():
                    dd["cand"][asn_year][term][role][field] = searching_ids(logger, candidates)

    for asn_year, fields in dd["comm"].items():
        for field, commission in fields.items():
            dd["comm"][asn_year][field] = searching_ids(logger, commission)

    return dd



