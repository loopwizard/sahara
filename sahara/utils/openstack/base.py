# Copyright (c) 2013 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils as json
from six.moves.urllib import parse as urlparse

from sahara import context
from sahara import exceptions as ex
from sahara.i18n import _
from sahara.i18n import _LE
from sahara.i18n import _LW

LOG = logging.getLogger(__name__)

# List of the errors, that can be retried
ERRORS_TO_RETRY = [408, 413, 429, 500, 502, 503, 504]

opts = [
    cfg.IntOpt('retries_number',
               default=5,
               help='Number of times to retry the request to client before '
                    'failing'),
    cfg.IntOpt('retry_after',
               default=10,
               help='Time between the retries to client (in seconds).')
]

retries = cfg.OptGroup(name='retries',
                       title='OpenStack clients calls retries')

CONF = cfg.CONF
CONF.register_group(retries)
CONF.register_opts(opts, group=retries)


def url_for(service_catalog, service_type, admin=False, endpoint_type=None):
    if not endpoint_type:
        endpoint_type = 'publicURL'
    if admin:
        endpoint_type = 'adminURL'

    service = _get_service_from_catalog(service_catalog, service_type)

    if service:
        endpoints = service['endpoints']
        if CONF.os_region_name:
            endpoints = [e for e in endpoints
                         if e['region'] == CONF.os_region_name]
        try:
            return _get_endpoint_url(endpoints, endpoint_type)
        except Exception:
            raise ex.SystemError(
                _("Endpoint with type %(type)s is not found for service "
                  "%(service)s")
                % {'type': endpoint_type,
                   'service': service_type})

    else:
        raise ex.SystemError(
            _('Service "%s" not found in service catalog') % service_type)


def _get_service_from_catalog(catalog, service_type):
    if catalog:
        catalog = json.loads(catalog)
        for service in catalog:
            if service['type'] == service_type:
                return service

    return None


def _get_endpoint_url(endpoints, endpoint_type):
    if 'interface' in endpoints[0]:
        endpoint_type = endpoint_type[0:-3]
        for endpoint in endpoints:
            if endpoint['interface'] == endpoint_type:
                return endpoint['url']
    return _get_case_insensitive(endpoints[0], endpoint_type)


def _get_case_insensitive(dictionary, key):
    for k, v in dictionary.items():
        if str(k).lower() == str(key).lower():
            return v

    # this will raise an exception as usual if key was not found
    return dictionary[key]


def retrieve_auth_url():
    info = urlparse.urlparse(context.current().auth_uri)
    version = 'v3' if CONF.use_identity_api_v3 else 'v2.0'

    return "%s://%s:%s/%s/" % (info.scheme, info.hostname, info.port, version)


def execute_with_retries(method, *args, **kwargs):
    attempts = CONF.retries.retries_number + 1
    while attempts > 0:
        try:
            return method(*args, **kwargs)
        except Exception as e:
            error_code = getattr(e, 'http_status', None) or getattr(
                e, 'status_code', None) or getattr(e, 'code', None)
            if error_code in ERRORS_TO_RETRY:
                LOG.warning(_LW('Occasional error occured during "{method}" '
                                'execution: {error_msg} ({error_code}). '
                                'Operation will be retried.').format(
                            method=method.__name__,
                            error_msg=e,
                            error_code=error_code))
                attempts -= 1
                retry_after = getattr(e, 'retry_after', 0)
                context.sleep(max(retry_after, CONF.retries.retry_after))
            else:
                LOG.error(_LE('Permanent error occured during "{method}" '
                              'execution: {error_msg}.').format(
                          method=method.__name__,
                          error_msg=e))
                raise e
    else:
        raise ex.MaxRetriesExceeded(attempts, method.__name__)
