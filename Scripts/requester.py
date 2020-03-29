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
            response = requests.post(auth_url, json=json.loads(request_body), headers=header) if not skip_ssl_verify \
                else requests.post(auth_url, json=json.loads(request_body), headers=header, verify=False)
            log.info("Authentication Response: {}".format(json.loads(response.text)))
            if response.status_code == 200:
                response_json = json.loads(response.text)
                return response_json.get('accessToken')

        except ConnectionError as e:
            log.error(e, exc_info=True)


# Create a feature class
class Feature:
    def __init__(self, feature_name, server_url, auth_url, api_key, user, test_cases, setup, cleanup):
        self.feature = feature_name
        self.api_key = api_key
        self.server_url = server_url
        self.auth_url = auth_url
        self.user = user
        self.tests = test_cases
        self.setup = setup
        self.cleanup = cleanup
        self.access_token = None
        self.test_vars_dict = {}
        self.test_cases = []

    def set_test_var_dict(self, step_name, request_output):
        """Create a variable dictionary to create dynamic response variable"""
        response_obj = json.loads(request_output.text)
        for step in self.setup:
            if step['name'] == step_name:
                response = step['response']
                for key, val in response.items():
                    if val in response_obj.keys():
                        if step_name == "login":
                            self.access_token = response_obj[val]
                        self.test_vars_dict[key] = response_obj[val]

    def expression_extractor(self, expression):
        new_expr = ""
        template_vars = re.findall(r"{{.+}}", expression)
        if len(template_vars) == 0:
            new_expr = expression
        for template_var in template_vars:
            target_var = template_var.strip('{{').strip('}}')
            list_of_vars = re.findall(r"\w+", target_var)
            if isinstance(self.test_vars_dict[list_of_vars[0]], str):
                t = Template(template_var)
                template_var_value = {target_var: self.test_vars_dict[target_var]}
                result = t.render(template_var_value)
            else:
                temp_target = self.test_vars_dict
                while not isinstance(temp_target, str):
                    for item in list_of_vars:
                        item = int(item) if item.isnumeric() else item
                        temp_target = temp_target[item]
                result = temp_target
            new_expr = expression.replace(template_var, result)
        return new_expr

    def evaluate_overridden_variable(self, template_var):
        new_expr = self.expression_extractor(template_var)
        result = json.loads(json.dumps(new_expr)) if len(re.findall(r"{.+}", new_expr)) > 0 else new_expr
        return result

    def test_setup(self):
        """Setup a before a test run"""
        # Extract data from the request object
        for step in self.setup:
            # if step is login then change current user
            access_token = self.access_token if self.access_token is not None else \
                self.user.get_access_token(self.server_url+self.auth_url)
            if step['request']['url'] is not None:
                val = self.evaluate_overridden_variable(step['request']['url'])
                step['request']['url'] = val

            if step['request']['headers'] is not None:
                for k, v in step['request']['headers'].items():
                    val = self.evaluate_overridden_variable(v)
                    step['request']['headers'][k] = val

            if step['request']['params'] is not None:
                for k, v in step['request']['params'].items():
                    val = self.evaluate_overridden_variable(v)
                    step['request']['params'][k] = val

            if step['request']['jsonOverrides'] is not None:
                for k, v in step['request']['jsonOverrides'].items():
                    val = self.evaluate_overridden_variable(v)
                    step['request']['jsonOverrides'][k] = val

            response = do_request(self.server_url, access_token, step['request'], skip_ssl=skip_ssl_verify) if \
                step['request']['jsonOverrides'] is None else  \
                do_request(self.server_url, access_token, step['request'], True, skip_ssl=skip_ssl_verify)
            self.set_test_var_dict(step['name'], response)
            log.info(response.text)

    def run(self):
        """Run all specific test"""
        self.test_setup()
        for test in self.tests:
            name = test['name']
            request = test['request']
            expected_response = test['response']
            authentication_url = self.server_url + self.auth_url
            test_case = Test(name, authentication_url, self.access_token, self.test_vars_dict, request, expected_response)
            test_case.actual_response = test_case.test_run(self.server_url, self.user)
            test_case.test_result = test_case.test_validation()
            self.test_cases.append(test_case)
        self.test_cleanup()

    def test_cleanup(self):
        """Setup a before a test run"""
        # Extract data from the request object
        for step in self.cleanup:
            # Add access token to the request header
            access_token = self.user.get_access_token(self.server_url + self.auth_url)
            if step['request']['url'] is not None:
                val = self.evaluate_overridden_variable(step['request']['url'])
                step['request']['url'] = val

            if step['request']['headers'] is not None:
                for k, v in step['request']['headers'].items():
                    val = self.evaluate_overridden_variable(v)
                    step['request']['headers'][k] = val

            if step['request']['params'] is not None:
                for k, v in step['request']['params'].items():
                    val = self.evaluate_overridden_variable(v)
                    step['request']['params'][k] = val

            if step['request']['jsonOverrides'] is not None:
                for k, v in step['request']['jsonOverrides'].items():
                    val = self.evaluate_overridden_variable(v)
                    step['request']['jsonOverrides'][k] = val

            response = do_request(self.server_url, access_token, step['request'], skip_ssl=skip_ssl_verify) if \
                step['request']['jsonOverrides'] is None else \
                do_request(self.server_url, access_token, step['request'], True, skip_ssl=skip_ssl_verify)
            log.info(response.text)


# Create a Test class
class Test:
    def __init__(self, name, auth_url, access_token, test_vars_dict, request, expected_response):
        self.name = name
        self.auth_url = auth_url
        self.request = request
        self.expected_response = expected_response
        self.actual_response = None
        self.access_token = access_token
        self.test_vars_dict = test_vars_dict
        self.test_result = None

    def expression_extractor(self, expression):
        new_expr = ""
        template_vars = re.findall(r"{{.+}}", expression)
        if len(template_vars) == 0:
            new_expr = expression
        for template_var in template_vars:
            target_var = template_var.strip('{{').strip('}}')
            list_of_vars = re.findall(r"\w+", target_var)
            if isinstance(self.test_vars_dict[list_of_vars[0]], str):
                t = Template(template_var)
                template_var_value = {target_var: self.test_vars_dict[target_var]}
                result = t.render(template_var_value)
            else:
                temp_target = self.test_vars_dict
                while not isinstance(temp_target, str):
                    for item in list_of_vars:
                        item = int(item) if item.isnumeric() else item
                        temp_target = temp_target[item]
                result = temp_target
            new_expr = expression.replace(template_var, result)
        return new_expr

    def evaluate_overridden_variable(self, template_var):
        new_expr = self.expression_extractor(template_var)
        result = json.loads(json.dumps(new_expr)) if len(re.findall(r"{.+}", new_expr)) > 0 else new_expr
        return result

    def test_run(self, server_url, user):
        access_token = self.access_token if self.access_token is not None else user.get_access_token(self.auth_url)
        if self.request['url'] is not None:
            val = self.evaluate_overridden_variable(self.request['url'])
            self.request['url'] = val

        if self.request['params'] is not None:
            for k, v in self.request['params'].items():
                val = self.evaluate_overridden_variable(v)
                self.request['params'][k] = val

        if self.request['headers'] is not None:
            for k, v in self.request['headers'].items():
                val = self.evaluate_overridden_variable(v)
                self.request['headers'][k] = val

        if self.request['jsonOverrides'] is not None:
            for k, v in self.request['jsonOverrides'].items():
                val = self.evaluate_overridden_variable(v)
                self.request['jsonOverrides'][k] = val
        response = do_request(server_url, access_token, self.request, skip_ssl=skip_ssl_verify) if \
            self.request['jsonOverrides'] is None else  \
            do_request(server_url, access_token, self.request, True, skip_ssl=skip_ssl_verify)

        return response

    def test_validation(self):
        try:
            # check status code first
            log.info("Expected Status Code:{}".format(self.expected_response['status']))
            log.info("Actual Status Code:{}".format(self.actual_response.status_code))
            assert self.expected_response['status'] == self.actual_response.status_code, "Status code doesn't match"
            json_validations = self.expected_response['jsonValidations']
            for key, val in json_validations.items():
                actual_val = read_response_body(json.loads(self.actual_response.text), key)
                assert val == actual_val, "Json Validation Failed"
            return "Passed"
        except AssertionError as error:
            log.exception(error)
            return "Failed"


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
        feature_obj = Feature(feature_name, server_url, auth_url, api_key, user_obj, tests, setup, cleanup)
    return feature_obj


def read_response_body(response_body, key):
    for k, v in response_body.items():
        json_path = key.split('.')
        if len(json_path) > 1:
            temp_target = response_body
            for item in json_path:
                if item == key:
                    return temp_target[key]
                temp_target = temp_target[item]
        else:
            return response_body[key]


def modify_request_body(original, replacement):
    for key, val in replacement.items():
        json_path = key.split('.')
        if len(json_path) > 1:
            temp_target = original
            temp_list = json_path
            for item in json_path:
                if len(temp_list) == 1:
                    break
                temp_list.remove(item)
                temp_target = temp_target[item]
            temp_target[val] = temp_target.pop(temp_list[0])
        else:
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


def do_request(server_url, access_token, yaml_request_dict, is_overridden=False, skip_ssl=False):
    try:
        request_url = server_url + yaml_request_dict['url']
        request_method = yaml_request_dict['method']
        request_params = yaml_request_dict['params']
        request_headers = yaml_request_dict['headers']
        request_headers['x-access-token'] = access_token
        request_json = get_request_body(yaml_request_dict, is_overridden)
        if request_method == "POST":
            request_body = json.dumps(request_json)
            response = requests.post(request_url, params=request_params, json=json.loads(request_body),
                                     headers=request_headers) if not skip_ssl else \
                requests.post(request_url, params=request_params, json=json.loads(request_body),
                              headers=request_headers, verify=False)
            log.info(response.text)
        elif request_method == "PUT":
            request_body = json.dumps(request_json)
            response = requests.put(request_url, params=request_params, json=json.loads(request_body),
                                    headers=request_headers) if not skip_ssl else \
                requests.put(request_url, params=request_params, json=json.loads(request_body),
                             headers=request_headers, verify=False)
            log.info(response.text)
        elif request_method == "GET":
            response = requests.get(request_url, params=request_params, headers=request_headers) if not skip_ssl else \
                requests.get(request_url, params=request_params, headers=request_headers, verify=False)
            log.info(response.text)
        elif request_method == "DELETE":
            response = requests.delete(request_url, params=request_params, headers=request_headers) if not skip_ssl else \
                requests.delete(request_url, params=request_params, headers=request_headers, verify=False)
            log.info(response.text)

        return response

    except KeyError as e:
        log.error(e, exc_info=True)
    except ConnectionError as e:
        log.error(e, exe_info=True)


base_dir = os.path.dirname(os.path.dirname(__file__))
config_folder = os.path.join(base_dir, 'testconfigs')
config_file = os.path.join(config_folder, "assets.yaml")
skip_ssl_verify = True

feature = config_parser(config_file)
feature.run()
