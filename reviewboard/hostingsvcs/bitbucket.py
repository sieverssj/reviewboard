from urllib import quote
from urllib2 import HTTPError, URLError

from django import forms
from django.utils import simplejson
from django.utils.translation import ugettext_lazy as _

from reviewboard.hostingsvcs.forms import HostingServiceForm
from reviewboard.hostingsvcs.service import HostingService
from reviewboard.scmtools.crypto import decrypt_password, encrypt_password
from reviewboard.scmtools.errors import FileNotFoundError


class BitbucketForm(HostingServiceForm):
    bitbucket_repo_name = forms.CharField(
        label=_('Repository name'),
        max_length=64,
        required=True,
        widget=forms.TextInput(attrs={'size': '60'}))


class Bitbucket(HostingService):
    """Hosting service support for Bitbucket.

    Bitbucket is a hosting service that supports Git and Mercurial
    repositories, and provides issue tracker support. It's available
    at https://www.bitbucket.org/.
    """
    name = 'Bitbucket'

    needs_authorization = True
    supports_repositories = True
    supports_bug_trackers = True

    form = BitbucketForm
    supported_scmtools = ['Git', 'Mercurial']
    repository_fields = {
        'Git': {
            'path': 'git@bitbucket.org:%(hosting_account_username)s/'
                    '%(bitbucket_repo_name)s.git',
            'mirror_path': 'https://%(hosting_account_username)s@'
                           'bitbucket.org/%(hosting_account_username)s/'
                           '%(bitbucket_repo_name)s.git',
        },
        'Mercurial': {
            'path': 'https://%(hosting_account_username)s@'
                    'bitbucket.org/%(hosting_account_username)s/'
                    '%(bitbucket_repo_name)s',
            'mirror_path': 'ssh://hg@bitbucket.org/'
                           '%(hosting_account_username)s/'
                           '%(bitbucket_repo_name)s',
        },
    }
    bug_tracker_field = ('https://bitbucket.org/'
                         '%(hosting_account_username)s/'
                         '%(bitbucket_repo_name)s/issue/%%s/')

    def check_repository(self, bitbucket_repo_name=None, *args, **kwargs):
        """Checks the validity of a repository.

        This will perform an API request against Bitbucket to get
        information on the repository. This will throw an exception if
        the repository was not found, and return cleanly if it was found.
        """
        self._api_get_repository(self.account.username, bitbucket_repo_name)

    def authorize(self, username, password, *args, **kwargs):
        """Authorizes the Bitbucket repository.

        Bitbucket supports HTTP Basic Auth or OAuth for the API. We use
        HTTP Basic Auth for now, and we store provided password,
        encrypted, for use in later API requests.
        """
        self.account.data['password'] = encrypt_password(password)
        self.account.save()

    def is_authorized(self):
        """Determines if the account has supported authorization tokens.

        This just checks if there's a password set on the account.
        """
        return self.account.data.get('password', None) is not None

    def get_file(self, repository, path, revision, base_commit_id=None,
                 *args, **kwargs):
        """Fetches a file from Bitbucket.

        This will perform an API request to fetch the contents of a file.

        If using Git, this will expect a base commit ID to be provided.
        """
        try:
            return self._api_get_src(repository, path, revision,
                                     base_commit_id)
        except (URLError, HTTPError):
            raise FileNotFoundError(path, revision)

    def get_file_exists(self, repository, path, revision, base_commit_id=None,
                        *args, **kwargs):
        """Determines if a file exists.

        This will perform an API request to fetch the metadata for a file.

        If using Git, this will expect a base commit ID to be provided.
        """
        try:
            self._api_get_src(repository, path, revision, base_commit_id)

            return True
        except (URLError, HTTPError, FileNotFoundError):
            return False

    def _api_get_repository(self, username, repo_name):
        url = self._build_api_url('repositories/%s/%s'
                                  % (username, repo_name))

        return self._api_get(url)

    def _api_get_src(self, repository, path, revision, base_commit_id):
        # If a base commit ID is provided, use it. It may not be provided,
        # though, and in this case, we need to use the provided revision,
        # which will work for Mercurial but not for Git.
        #
        # If not provided, and using Git, we'll give the user a File Not
        # Found error with some info on what they need to do to correct
        # this.
        if base_commit_id:
            revision = base_commit_id
        elif repository.tool.name == 'Git':
            raise FileNotFoundError(
                path,
                revision,
                detail='The necessary revision information needed to find '
                       'this file was not provided. Use RBTools 0.5.2 or '
                       'newer.')

        url = self._build_api_url(
            'repositories/%s/%s/raw/%s/%s'
            % (self.account.username,
               quote(self._get_repository_name(repository)),
               quote(revision),
               quote(path)))

        return self._api_get(url, raw_content=True)

    def _build_api_url(self, url):
        return 'https://bitbucket.org/api/1.0/%s' % url

    def _get_repository_name(self, repository):
        return repository.extra_data['bitbucket_repo_name']

    def _api_get(self, url, raw_content=False):
        try:
            data, headers = self._http_get(
                url,
                username=self.account.username,
                password=decrypt_password(self.account.data['password']))

            if raw_content:
                return data
            else:
                return simplejson.loads(data)
        except HTTPError, e:
            # Bitbucket's API documentation doesn't provide any information
            # on an error structure, and the API browser shows that we
            # sometimes get a raw error string, and sometimes raw HTML.
            # We'll just have to return what we get for now.
            raise Exception(e.read())
