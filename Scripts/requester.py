import os
import yaml
import requests
import logging
import json
import re
from jinja2 import Template


log = logging.getLogger(__name__)


# Create a user class
class User:
    def __init__(self, user_id, password):
        self.user_id = user_id
        self.password = password

    def get_access_token(self, auth_url):
        request_body = {'username': self.user_id, 'password': self.password}
        request_body = json.dumps(request_body)
        header = {'Content-Type': 'application/json'}
        try:
            response = requests.post(auth_url, json=json.loads(request_body), headers=header)
            log.info("Authentication Response: {}".format(json.loads(response.text)))
            if response.status_code == 200:
                response_json = json.loads(response.text)
                return response_json.get('accessToken')

        except ConnectionError as e:
            log.error(e, exc_info=True)


# Create a feature class
class Feature:
    def __init__(self, feature_name, server_url, auth_url, api_key, user, tests):
        self.feature = feature_name
        self.api_key = api_key
        self.server_url = server_url
        self.auth_url = auth_url
        self.user = user
        self.tests = tests


# Create a Test class
class Test:
    def __init__(self, name, auth_url, setup, cleanup, request, expected_response):
        self.name = name
        self.auth_url = auth_url
        self.request = request
        self.expected_response = expected_response
        self.setup = setup
        self.cleanup = cleanup
        self.test_vars_dict = {}

    def set_test_var_dict(self, step_name, request_output):
        """Create a variable dictionary to create dynamic response variable"""
        response_obj = json.loads(request_output.text)
        for step in self.setup:
            if step['name'] == step_name:
                response = step['response']
                for key, val in response.items():
                    if val in response_obj.keys():
                        self.test_vars_dict[key] = response_obj[val]

    def evaluate_overridden_variable(self, template_var):
        result = ""
        template_vars = re.findall(r"{{\w+}}", template_var)
        target_var = template_vars[0].strip('{{').strip('}}') if len(template_vars) > 0 else template_var
        if len(template_vars) > 0:
            t = Template(template_var)
            template_var_value = {target_var: self.test_vars_dict[target_var]}
            result = t.render(template_var_value)
        else:
            result = template_var
        return target_var,result

    def test_setup(self, server_url, user):
        """Setup a before a test run"""
        # Extract data from the request object
        for step in self.setup:
            # Add access token to the request header
            access_token = user.get_access_token(self.auth_url)
            if step['request']['jsonOverrides'] is not None:
                for k, v in step['request']['jsonOverrides'].items():
                    key, val = self.evaluate_overridden_variable(v)
                    step['request']['jsonOverrides'][key] = val
            response = do_request(server_url, access_token, step['request']) if step['request']['jsonOverrides'] is None else  \
                do_request(server_url, access_token, step['request'], True)
            self.set_test_var_dict(response)
            log.info(response.text)

    def run(self, server_url, user):
        """Run a specific test"""
        # Extract data from the request object
        self.test_setup(server_url, user)
        if self.setup_user is not None:
            access_token = user.get_access_token(self.auth_url)
            response = do_request(server_url, access_token, self.request, is_overridden=True)
            log.info(response.text)
        self.test_cleanup(server_url, user)

    def test_cleanup(self, server_url, user):
        """Setup a before a test run"""
        # Extract data from the request object
        for step in self.cleanup:
            # Add access token to the request header
            access_token = user.get_access_token(self.auth_url)

            response = do_request(server_url, access_token, step)
            log.info(response.text)


# Parse the YAML config file
def config_parser(config):
    test_cases = []
    with open(config) as f:
        data = yaml.load(f, Loader=yaml.FullLoader)
        feature_name = data['name']
        api_key = data['apikey']
        server_url = data['serverurl']
        auth_url = data['authurl']
        user = data['user']
        user_obj = User(user['id'], user['password'])
        tests = data['testCases']
        setup = data['testSetup']
        cleanup = data['testCleanup']
        for test in tests:
            name = test['name']
            request = test['request']
            expected_response = test['response']
            authentication_url = server_url + auth_url
            test_case = Test(name, authentication_url, setup, cleanup, request, expected_response)
            test_cases.append(test_case)
        feature_obj = Feature(feature_name, server_url, auth_url, api_key, user_obj, test_cases)
    return feature_obj


def modify_request_body(original, replacement):
    for key, val in replacement.items():
        if key in original.keys():
            original[key] = replacement[key]
    return original


def get_request_body(yaml_request_dict, is_overridden=True):
    try:
        root_dir = os.path.dirname(os.path.dirname(__file__))
        test_data_folder = os.path.join(root_dir, 'testdata')
        request_json = ""
        if yaml_request_dict['baseJson'] is not None:
            json_file = os.path.join(test_data_folder, yaml_request_dict['baseJson'])
            with open(json_file) as f:
                request_json = json.load(f)
                if is_overridden:
                    overridden_json = yaml_request_dict['jsonOverrides']
                    request_json = modify_request_body(request_json, overridden_json)

        return request_json

    except KeyError as e:
        log.error(e, exc_info=True)


def do_request(server_url, access_token, yaml_request_dict, is_overridden=False):
    try:
        request_url = server_url + yaml_request_dict['url']
        request_method = yaml_request_dict['method']
        request_params = yaml_request_dict['params']
        request_headers = yaml_request_dict['headers']
        request_headers['x-access-token'] = access_token
        request_json = get_request_body(yaml_request_dict, is_overridden)
        if request_method == "POST" or request_method == "PUT":
            request_body = json.dumps(request_json)
            response = requests.post(request_url, params=request_params, json=json.loads(request_body),
                                     headers=request_headers)
            log.info(response.text)

        return response

    except KeyError as e:
        log.error(e, exc_info=True)
    except ConnectionError as e:
        log.error(e, exe_info=True)


base_dir = os.path.dirname(os.path.dirname(__file__))
config_folder = os.path.join(base_dir, 'testconfigs')
config_file = os.path.join(config_folder, "assets.yaml")

feature = config_parser(config_file)
for test in feature.tests:
    test.run(feature.server_url, feature.user)
