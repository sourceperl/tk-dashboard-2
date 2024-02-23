from base64 import b64encode
import ssl
from urllib.parse import urljoin, urlparse, quote, unquote
from urllib.request import Request, urlopen
from xml.dom import minidom
import dateutil.parser


# some const
HASH_BUF_SIZE = 64 * 1024
HTTP_OK = 200
HTTP_CREATED = 201
HTTP_NO_CONTENT = 204
HTTP_MULTI_STATUS = 207
HTTP_UNAUTHORIZED = 401
HTTP_NOT_ALLOWED = 405


# some class
class WebDAVError(Exception):
    pass


class WebDAV:
    def __init__(self, url: str, username: str = '', password: str = '', timeout: float = 5.0):
        # public
        self.url = url
        self.timeout = timeout
        self.last_http_code = 0
        # auth
        b64_credential = b64encode(f'{username}:{password}'.encode()).decode()
        self._base_headers_d = {'Authorization': f'Basic {b64_credential}'}
        # ssl context with certificate verfication disabled
        ctx_ssl = ssl.create_default_context()
        ctx_ssl.check_hostname = False
        ctx_ssl.verify_mode = ssl.CERT_NONE
        self._ctx_ssl = ctx_ssl

    def upload(self, file_path: str, content: bytes = b'') -> None:
        # do request
        req = Request(urljoin(self.url, quote(file_path)), data=content, headers=self._base_headers_d, method='PUT')
        uo_ret = urlopen(req, timeout=self.timeout, context=self._ctx_ssl)
        self.last_http_code = uo_ret.status
        # raise WebDAVError if request failed
        # HTTP_CREATED => create file, HTTP_NO_CONTENT => update an existing file
        if not (uo_ret.status == HTTP_CREATED or uo_ret.status == HTTP_NO_CONTENT):
            raise WebDAVError('Error during upload of file "{file_path}" (HTTP code is {uo_ret.status})')

    def download(self, file_path: str) -> bytes:
        # do request
        req = Request(urljoin(self.url, quote(file_path)), headers=self._base_headers_d, method='GET')
        uo_ret = urlopen(req, timeout=self.timeout, context=self._ctx_ssl)
        self.last_http_code = uo_ret.status
        # return file content if success, raise WebDAVError if request failed
        if uo_ret.status == HTTP_OK:
            return uo_ret.read()
        else:
            raise WebDAVError('Error during download of file "{file_path}" (HTTP code is {uo_ret.status})')

    def delete(self, file_path: str) -> None:
        # do request
        req = Request(urljoin(self.url, quote(file_path)), headers=self._base_headers_d, method='DELETE')
        uo_ret = urlopen(req, timeout=self.timeout, context=self._ctx_ssl)
        self.last_http_code = uo_ret.status
        # raise WebDAVError if request failed
        if uo_ret.status != HTTP_NO_CONTENT:
            raise WebDAVError(f'Error during deletion of file "{file_path}" (HTTP code is {uo_ret.status})')

    def mkdir(self, dir_path: str) -> None:
        # do request
        req = Request(urljoin(self.url, quote(dir_path)), headers=self._base_headers_d, method='MKCOL')
        uo_ret = urlopen(req, timeout=self.timeout, context=self._ctx_ssl)
        self.last_http_code = uo_ret.status
        # raise WebDAVError if request failed
        if uo_ret.status != HTTP_CREATED:
            raise WebDAVError(f'Error during creation of dir "{dir_path}" (HTTP code is {uo_ret.status})')

    def ls(self, path: str = '', depth: int = 1) -> list:
        # build xml message
        propfind_req = '<?xml version="1.0" encoding="utf-8" ?>' \
            '<d:propfind xmlns:d="DAV:">' \
            '<d:prop><d:getlastmodified/><d:getcontentlength/></d:prop> ' \
            '</d:propfind>'
        # do request
        req_headers_d = self._base_headers_d
        req_headers_d['Depth'] = str(depth)
        # request
        req = Request(urljoin(self.url, quote(path)), data=propfind_req.encode(),
                      headers=req_headers_d, method='PROPFIND')
        uo_ret = urlopen(req, timeout=self.timeout, context=self._ctx_ssl)
        self.last_http_code = uo_ret.status
        # check result
        if self.last_http_code == HTTP_MULTI_STATUS:
            # return a list of dict
            results_l = []
            # parse XML
            # dom = minidom.parseString(r.text.encode('ascii', 'xmlcharrefreplace'))
            dom = minidom.parseString(uo_ret.read())  # .encode('ascii', 'xmlcharrefreplace'))
            # for every d:response
            for response in dom.getElementsByTagName('d:response'):
                # in d:response/d:propstat/d:prop
                prop_stat = response.getElementsByTagName('d:propstat')[0]
                prop = prop_stat.getElementsByTagName('d:prop')[0]
                # d:getlastmodified
                get_last_modified = prop.getElementsByTagName('d:getlastmodified')[0].firstChild.data
                dt_last_modified = dateutil.parser.parse(get_last_modified)
                # d:getcontentlength
                try:
                    content_length = int(prop.getElementsByTagName('d:getcontentlength')[0].firstChild.data)
                except IndexError:
                    content_length = 0
                # href at d:response level
                href = response.getElementsByTagName('d:href')[0].firstChild.data
                # convert href to file path
                if href.startswith(self.url):
                    href = href[len(self.url):]
                elif href.startswith(urlparse(self.url).path):
                    href = href[len(urlparse(self.url).path):]
                file_path = unquote(href)
                file_path = file_path[len(path):]
                # feed result list
                results_l.append(dict(file_path=file_path, content_length=content_length,
                                      dt_last_modified=dt_last_modified))
            return results_l
        else:
            raise WebDAVError(f'Error during PROPFIND (ls) request (HTTP code is {self.last_http_code})')
