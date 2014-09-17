import sys
import json
import base64
import hashlib
import re
import urllib
import urllib2
import xbmc
import xbmcaddon
import bencode
from threading import Thread
import Queue
import CommonFunctions

PAYLOAD = json.loads(base64.b64decode(sys.argv[1]))

# Addon Script information
__addonID__ = str(sys.argv[0])
__addon__ = xbmcaddon.Addon(__addonID__)
__baseUrl__ = __addon__.getSetting("base_url")

# ParseDOM init
common = CommonFunctions
common.plugin = __addonID__

ACTION_SEARCH = "recherche.php"
ACTION_FILMS = "films"
ACTION_SERIES = "series"
CATEGORY_FILMS = "<strong>Films</strong>"
CATEGORY_SERIES = "<strong>Series</strong>"
USERAGENT = "Mozilla/5.0 (X11; U; Linux i686) Gecko/20071127 Firefox/2.0.0.11"

class HeadRequest(urllib2.Request):
    def get_method(self):
        return "HEAD"

# Direct link - Use with Threading queue
def directLink(url, q):
    xbmc.log('directLink URL : %s' % url, xbmc.LOGDEBUG)
    url = urllib2.Request(url)
    url.add_header('User-Agent', USERAGENT)
    response = urllib2.urlopen(url)
    data = response.read()
    if response.headers.get("Content-Encoding", "") == "gzip":
        import zlib
        data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data)
    q.put([{"uri": magnet} for magnet in re.findall(r'magnet:\?[^\'"\s<>\[\]]+', data)])

# Search and return JSON results
def jsonSearch(query):
    url = "%s/%s?ajax&query=%s" % (__baseUrl__, ACTION_SEARCH, urllib.quote_plus(query))
    xbmc.log('jsonSearch : %s' % url, xbmc.LOGDEBUG)
    url = urllib2.Request(url)
    url.add_header('User-Agent', USERAGENT)
    response = urllib2.urlopen(url)
    data = response.read()
    if response.headers.get("Content-Encoding", "") == "gzip":
        import zlib
        data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data)
    return data

# Default Search
def search(query):
    url = "%s/%s?query=%s" % (__baseUrl__, ACTION_SEARCH, urllib.quote_plus(query))
    xbmc.log('Search : %s' % url, xbmc.LOGDEBUG)
    req = urllib2.Request(url)
    req.add_header('User-Agent', USERAGENT)
    response = urllib2.urlopen(req)
    data = response.read()
    if response.headers.get("Content-Encoding", "") == "gzip":
        import zlib
        data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data)
    if response.geturl() is not url:
        # Redirection 30x followed to individual page - Return the magnet link
        xbmc.log('Redirection 30x followed to individual page - Return the magnet link', xbmc.LOGDEBUG)
        return [{"uri": magnet} for magnet in re.findall(r'magnet:\?[^\'"\s<>\[\]]+', data)]
    else:
        # Multiple torrent page - Parse page to get individual page
        xbmc.log('Multiple torrent page - Parsing', xbmc.LOGDEBUG)
        # Parse the table result
        table = common.parseDOM(data, 'table', attrs = { "class": "table_corps" })
        liens = common.parseDOM(table, 'a', attrs = { "class": "torrent" }, ret = 'href')
        xbmc.log('liens : %s' % liens, xbmc.LOGDEBUG)
        threads = []
        magnets = []
        q = Queue.Queue()

        # Call each individual page in parallel
        for lien in liens :
            thread = Thread(target=directLink, args = ('%s%s' % (__baseUrl__, lien), q))
            thread.start()
            threads.append(thread)

        # And get all the results
        for t in threads :
            t.join()
        while not q.empty():
            magnets.append(q.get()[0])

        xbmc.log('Magnets List : %s' % magnets)
        return magnets

def search_episode(imdb_id, tvdb_id, name, season, episode): 
    results = json.loads(jsonSearch(name))
    for result in results:
        if result["category"] == CATEGORY_SERIES :
            # Get show's individual url
            url = "%s/%s?query=%s" % (__baseUrl__, ACTION_SEARCH, urllib.quote_plus(result["label"]))
            if season is not 1 :
                # Get model url for requested season
                xbmc.log('Season URL: %s' % url, xbmc.LOGDEBUG)
                req = urllib2.Request(url)
                req.add_header('User-Agent', USERAGENT)
                response = urllib2.urlopen(req)
                # Replace "season" data in url.  Ex.  :
                # http://www.omgtorrent.com/series/true-blood_saison_7_53.html
                url = response.geturl().replace("_1_","_%s_" % season)
            # Parse season specific page
            return parse_season(url,episode)

def search_movie(imdb_id, name, year):
    results = json.loads(jsonSearch(name))
    xbmc.log('Search Movie JSon results: %s' % results, xbmc.LOGDEBUG)
    for result in results:
        if result["category"] == CATEGORY_FILMS:
            # Get movie's page
            return search(result["label"])

def torrent2magnet(torrent_url, q):
    req = urllib2.Request(torrent_url)
    req.add_header('User-Agent', USERAGENT)
    response = urllib2.urlopen(req)
    torrent = response.read()
    metadata = bencode.bdecode(torrent)
    hashcontents = bencode.bencode(metadata['info'])
    digest = hashlib.sha1(hashcontents).digest()
    b32hash = base64.b32encode(digest)
    magneturl = 'magnet:?xt=urn:btih:' + b32hash + '&dn=' + metadata['info']['name']
    xbmc.log('Put Magnet in queue : %s' % magneturl, xbmc.LOGDEBUG)
    q.put(magneturl)

def parse_season(url, episode):
    result = []
    threads = []
    q = Queue.Queue()
    url = urllib2.Request(url)
    url.add_header('User-Agent', USERAGENT)
    response = urllib2.urlopen(url)
    data = response.read()
    if response.headers.get("Content-Encoding", "") == "gzip":
        import zlib
        data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data)
    # Get torrent (if any) from table - 1 line per episode
    table = common.parseDOM(data, 'table', attrs = { "class": "table_corps" })
    liens = common.parseDOM(table, 'tr', attrs = { "class": "bords" })
    if liens :
        # Get the first known episode
        start = int(common.parseDOM(liens[0], 'td')[0].rstrip('.'))
        for torrent in common.parseDOM(liens[episode - start], 'a', ret = 'href') :
            # Call each individual page in parallel
            thread = Thread(target=torrent2magnet, args = ('%s%s' % (__baseUrl__, torrent), q))
            thread.start()
            threads.append(thread)
        
        # And get all the results
        for t in threads :
            t.join()
        while not q.empty():
            result.append({"uri": q.get()})
    return result

urllib2.urlopen(PAYLOAD["callback_url"],
    data=json.dumps(globals()[PAYLOAD["method"]](*PAYLOAD["args"])))
