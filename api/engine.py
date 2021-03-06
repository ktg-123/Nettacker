# !/usr/bin/env python
# -*- coding: utf-8 -*-

import multiprocessing
import time
import random
import csv
import json
import string
from datetime import datetime
from database.db import create_connection, __logs_by_scan_id
from database.models import HostsLog, Report
import os
from flask import Flask
from flask import jsonify
from flask import request as flask_request
from flask import render_template
from flask import abort
from flask import escape
from flask import Response
from flask import make_response
from core.alert import write_to_api_console
from core.alert import messages
from core._die import __die_success
from core._die import __die_failure
from core._time import now
from core.compatible import version
from core.load_modules import load_all_method_args
from core.config import _core_config
from core.config_builder import _core_default_config
from core.config_builder import _builder
from api.api_core import __structure
from api.api_core import __get_value
from api.api_core import root_dir
from api.api_core import get_file
from api.api_core import __mime_types
from api.api_core import __scan_methods
from api.api_core import __profiles
from api.api_core import __graphs
from api.api_core import __languages
from api.api_core import __remove_non_api_keys
from api.api_core import __rules
from api.api_core import __api_key_check
from api.__start_scan import __scan
from database.db import __select_results
from database.db import __get_result
from database.db import __last_host_logs
from database.db import __logs_to_report_json
from database.db import __search_logs
from database.db import __logs_to_report_html
from core.targets import target_type


TEMPLATE_DIR = os.path.join(os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "web"), "static")
app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.config.from_object(__name__)


def __language(app=app):
    """
    find the language in config

    Args:
        app: flask app

    Returns:
        the language in string
    """
    return app.config["OWASP_NETTACKER_CONFIG"]["language"]


@app.errorhandler(400)
def error_400(error):
    """
    handle the 400 HTTP error

    Args:
        error: the flask error

    Returns:
        400 JSON error
    """
    return jsonify(__structure(status="error", msg=error.description)), 400


@app.errorhandler(401)
def error_401(error):
    """
    handle the 401 HTTP error

    Args:
        error: the flask error

    Returns:
        401 JSON error
    """
    return jsonify(__structure(status="error", msg=error.description)), 401


@app.errorhandler(403)
def error_403(error):
    """
    handle the 403 HTTP error

    Args:
        error: the flask error

    Returns:
        403 JSON error
    """
    return jsonify(__structure(status="error", msg=error.description)), 403


@app.errorhandler(404)
def error_404(error):
    """
    handle the 404 HTTP error

    Args:
        error: the flask error

    Returns:
        404 JSON error
    """
    return jsonify(__structure(status="error",
                               msg=messages(app.config[
                                   "OWASP_NETTACKER_CONFIG"]["language"],
                                   "not_found"))), 404


@app.before_request
def limit_remote_addr():
    """
    check if IP filtering applied and API address is in whitelist

    Returns:
        None if it's in whitelist otherwise abort(403)
    """
    # IP Limitation
    if app.config["OWASP_NETTACKER_CONFIG"]["api_client_white_list"]:
        if flask_request.remote_addr not in app.config[
                "OWASP_NETTACKER_CONFIG"]["api_client_white_list_ips"]:
            abort(403, messages(__language(), "unauthorized_IP"))
    return


@app.after_request
def access_log(response):
    """
    if access log enabled, its writing the logs

    Args:
        response: the flask response

    Returns:
        the flask response
    """
    if app.config["OWASP_NETTACKER_CONFIG"]["api_access_log"]:
        r_log = open(app.config["OWASP_NETTACKER_CONFIG"]["api_access_log_filename"], "ab")
        # if you need to log POST data
        # r_log.write(
        #     "{0} [{1}] {2} \"{3} {4}\" {5} {6} {7}\r\n".format(
        #                                                      flask_request.remote_addr,
        #                                                      now(),
        #                                                      flask_request.host,
        #                                                      flask_request.method,
        #                                                      flask_request.full_path,
        #                                                      flask_request.user_agent,
        #                                                      response.status_code,
        #                                                      json.dumps(flask_request.form)))
        r_log.write("{0} [{1}] {2} \"{3} {4}\" {5} {6}\r\n".format(
            flask_request.remote_addr, now(),
            flask_request.host,
            flask_request.method, flask_request.full_path,
            flask_request.user_agent, response.status_code))
        r_log.close()
    return response


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def get_statics(path):
    """
    getting static files and return content mime types

    Args:
        path: path and filename

    Returns:
        file content and content type if file found otherwise abort(404)
    """
    static_types = __mime_types()
    return Response(get_file(os.path.join(root_dir(), path)), mimetype=static_types.get(os.path.splitext(path)[1], "text/html"))


@app.route("/", methods=["GET", "POST"])
def index():
    """
    index page for WebUI

    Returns:
        rendered HTML page
    """
    filename = _builder(_core_config(), _core_default_config())["log_in_file"]

    if version() == 2:
        return render_template("index.html", scan_method=__scan_methods(),
                               profile=__profiles(), graphs=__graphs(),
                               languages=__languages(), filename=filename,
                               method_args_list=load_all_method_args(
                                   __language(), API=True).decode('utf-8'))

    return render_template("index.html", scan_method=__scan_methods(),
                           profile=__profiles(), graphs=__graphs(),
                           languages=__languages(), filename=filename,
                           method_args_list=load_all_method_args(
        __language(), API=True))


@app.route("/new/scan", methods=["GET", "POST"])
def new_scan():
    """
    new scan through the API

    Returns:
        a JSON message with scan details if success otherwise a JSON error
    """
    _start_scan_config = {}
    __api_key_check(app, flask_request, __language())
    targetValue = __get_value(flask_request, "targets")
    if(target_type(targetValue) == "UNKNOWN"):
        return jsonify({"error": "Please input correct target"}), 400
    for key in _core_default_config():
        if __get_value(flask_request, key) is not None:
            _start_scan_config[key] = escape(__get_value(flask_request, key))
    _start_scan_config["backup_ports"] = __get_value(flask_request, "ports")
    _start_scan_config = __rules(__remove_non_api_keys(_builder(_start_scan_config,
                                                                _builder(_core_config(), _core_default_config()))),
                                 _core_default_config(), __language())
    _p = multiprocessing.Process(target=__scan, args=[_start_scan_config])
    _p.start()
    # Sometimes method_args is too big!
    _start_scan_config["methods_args"] = {
        "as_user_set": "set_successfully"
    }
    return jsonify(_start_scan_config), 200


@app.route("/session/check", methods=["GET"])
def __session_check():
    """
    check the session if it's valid

    Returns:
        a JSON message if it's valid otherwise abort(401)
    """
    __api_key_check(app, flask_request, __language())
    return jsonify(__structure(status="ok", msg=messages(
        __language(), "browser_session_valid"))), 200


@app.route("/session/set", methods=["GET"])
def __session_set():
    """
    set session on the browser

    Returns:
        200 HTTP response if session is valid and a set-cookie in the
        response if success otherwise abort(403)
    """
    __api_key_check(app, flask_request, __language())
    res = make_response(
        jsonify(__structure(status="ok", msg=messages(
            __language(), "browser_session_valid"))))
    res.set_cookie("key", value=app.config[
        "OWASP_NETTACKER_CONFIG"]["api_access_key"])
    return res


@app.route("/session/kill", methods=["GET"])
def __session_kill():
    """
    unset session on the browser

    Returns:
        a 200 HTTP response with set-cookie to "expired"
        to unset the cookie on the browser
    """
    res = make_response(
        jsonify(__structure(status="ok", msg=messages(
            __language(), "browser_session_killed"))))
    res.set_cookie("key", "", expires=0)
    return res


@app.route("/results/get_list", methods=["GET"])
def __get_results():
    """
    get list of scan's results through the API

    Returns:
        an array of JSON scan's results if success otherwise abort(403)
    """
    __api_key_check(app, flask_request, __language())
    try:
        page = int(__get_value(flask_request, "page"))
    except Exception:
        page = 1
    return jsonify(__select_results(__language(), page)), 200


@app.route("/results/get", methods=["GET"])
def __get_result_content():
    """
    get a result HTML/TEXT/JSON content

    Returns:
        content of the scan result
    """
    __api_key_check(app, flask_request, __language())
    try:
        _id = int(__get_value(flask_request, "id"))
    except Exception:
        return jsonify(__structure(status="error", msg="your scan id is not valid!")), 400
    return __get_result(__language(), _id)

@app.route("/results/get_json", methods=["GET"])
def __get_results_json():
    """
    get host's logs through the API in JSON type

    Returns:
        an array with JSON events
    """
    session = create_connection(__language())
    __api_key_check(app, flask_request, __language())
    try:
        _id = int(__get_value(flask_request, "id"))
        scan_id_temp = session.query(Report).filter(Report.id == _id).all()
        print(type(scan_id_temp))
    except Exception as _:
        _id = ""
    if(scan_id_temp):
        result_id = session.query(Report).join(HostsLog, Report.scan_id == HostsLog.scan_id).filter(Report.scan_id == scan_id_temp[0].scan_id).all()
    else:
        result_id = []
    json_object = {}
    if(result_id):
        scan_id = result_id[0].scan_id
        data = __logs_by_scan_id(scan_id, __language())
        json_object = json.dumps(data)
    date_from_db = scan_id_temp[0].date
    date_format = datetime.strptime(date_from_db, "%Y-%m-%d %H:%M:%S")
    date_format = str(date_format).replace("-", "_").replace(":", "_").replace(" ", "_")
    filename = "report-" + date_format +"".join(random.choice(string.ascii_lowercase) for x in range(10))
    return Response(json_object, mimetype='application/json', headers={'Content-Disposition':'attachment;filename='+filename+'.json'})

@app.route("/results/get_csv", methods=["GET"])
def __get_results_csv():
    """
    get host's logs through the API in JSON type

    Returns:
        an array with JSON events
    """
    session = create_connection(__language())
    __api_key_check(app, flask_request, __language())
    try:
        _id = int(__get_value(flask_request, "id"))
        scan_id_temp = session.query(Report).filter(Report.id == _id).all()
    except Exception as _:
        _id = ""
    if(scan_id_temp):
        result_id = session.query(Report).join(HostsLog, Report.scan_id == HostsLog.scan_id).filter(Report.scan_id == scan_id_temp[0].scan_id).all()
    else:
        result_id = []
    date_from_db = scan_id_temp[0].date
    date_format = datetime.strptime(date_from_db, "%Y-%m-%d %H:%M:%S")
    date_format = str(date_format).replace("-", "_").replace(":", "_").replace(" ", "_")
    filename = "report-" + date_format+"".join(random.choice(string.ascii_lowercase) for x in range(10))
    _reader = ''
    if(result_id):
        scan_id = result_id[0].scan_id
        data = __logs_by_scan_id(scan_id, __language())
        keys = data[0].keys()
        with open(filename, "w")  as output_file:
            dict_writer = csv.DictWriter(output_file, fieldnames=keys, quoting=csv.QUOTE_ALL)
            dict_writer.writeheader()
            for i in data:
                dictdata = {key: value for key, value in i.items()
                            if key in keys}
                dict_writer.writerow(dictdata)
        print_data = []
        with open(filename, 'r') as output_file:
            _reader = output_file.read()
    return Response(_reader, mimetype='text/csv', headers={'Content-Disposition':'attachment;filename='+filename+'.csv'})


@app.route("/logs/get_list", methods=["GET"])
def __get_last_host_logs():
    """
    get list of logs through the API

    Returns:
        an array of JSON logs if success otherwise abort(403)
    """
    __api_key_check(app, flask_request, __language())
    try:
        page = int(__get_value(flask_request, "page"))
    except Exception:
        page = 1
    return jsonify(__last_host_logs(__language(), page)), 200


@app.route("/logs/get_html", methods=["GET"])
def __get_logs_html():
    """
    get host's logs through the API in HTML type

    Returns:
        HTML report
    """
    __api_key_check(app, flask_request, __language())
    try:
        host = __get_value(flask_request, "host")
    except Exception:
        host = ""
    return make_response(__logs_to_report_html(host, __language()))


@app.route("/logs/get_json", methods=["GET"])
def __get_logs():
    """
    get host's logs through the API in JSON type

    Returns:
        an array with JSON events
    """
    __api_key_check(app, flask_request, __language())
    try:
        host = __get_value(flask_request, "host")
    except Exception:
        host = ""
    data = __logs_to_report_json(host, __language())
    json_object = json.dumps(data)
    filename = "report-" + now(model="%Y_%m_%d_%H_%M_%S")+"".join(random.choice(string.ascii_lowercase) for x in range(10))
    return Response(json_object, mimetype='application/json', headers={'Content-Disposition':'attachment;filename='+filename+'.json'})



@app.route("/logs/get_csv", methods=["GET"])
def __get_logs_csv():
    """
    get host's logs through the API in JSON type

    Returns:
        an array with JSON events
    """
    __api_key_check(app, flask_request, __language())
    try:
        host = __get_value(flask_request, "host")
    except Exception:
        host = ""
    data = __logs_to_report_json(host, __language())
    keys = data[0].keys()
    filename = "report-" + now(model="%Y_%m_%d_%H_%M_%S")+"".join(random.choice(string.ascii_lowercase) for x in range(10))
    with open(filename, "w")  as output_file:
        dict_writer = csv.DictWriter(output_file, fieldnames=keys, quoting=csv.QUOTE_ALL)
        dict_writer.writeheader()
        for i in data:
            dictdata = {key: value for key, value in i.items()
                        if key in keys}
            dict_writer.writerow(dictdata)
    print_data = []
    with open(filename, 'r') as output_file:
        reader = output_file.read()
    return Response(reader, mimetype='text/csv', headers={'Content-Disposition':'attachment;filename='+filename+'.csv'})

@app.route("/logs/search", methods=["GET"])
def ___go_for_search_logs():
    """
    search in all events

    Returns:
        an array with JSON events
    """
    __api_key_check(app, flask_request, __language())
    try:
        page = int(__get_value(flask_request, "page"))
    except Exception:
        page = 1
    try:
        query = __get_value(flask_request, "q")
    except Exception:
        query = ""
    return jsonify(__search_logs(__language(), page, query)), 200


def __process_it(api_host, api_port, api_debug_mode, api_access_key,
                 api_client_white_list, api_client_white_list_ips,
                 api_access_log, api_access_log_filename, api_cert,
                 api_cert_key, language):
    """
    a function to run flask in a subprocess to make kill signal in a better
    way!

    Args:
        api_host: host/IP to bind address
        api_port: bind port
        api_debug_mode: debug mode flag
        api_access_key: API access key
        api_client_white_list: clients while list flag
        api_client_white_list_ips: clients white list IPs
        api_access_log: access log flag
        api_access_log_filename: access log filename
        api_cert: SSL certificate
        api_cert_key: SSL Private key

        language: language
    """
    app.config["OWASP_NETTACKER_CONFIG"] = {
        "api_access_key": api_access_key,
        "api_client_white_list": api_client_white_list,
        "api_client_white_list_ips": api_client_white_list_ips,
        "api_access_log": api_access_log,
        "api_access_log_filename": api_access_log_filename,
        "api_cert": api_cert,
        "api_cert_key": api_cert_key,
        "language": language
    }
    try:
        if api_cert:
            if api_cert_key:
                app.run(host=api_host, port=api_port, debug=api_debug_mode,
                        ssl_context=(api_cert, api_cert_key), threaded=True)
            else:
                __die_failure(messages(language, "api_cert_key"))

        if api_cert_key:
            if api_cert:
                app.run(host=api_host, port=api_port, debug=api_debug_mode,
                        ssl_context=(api_cert, api_cert_key), threaded=True)
            else:
                __die_failure(messages(language, "api_cert"))

        else:
            app.run(host=api_host, port=api_port, debug=api_debug_mode, ssl_context="adhoc", threaded=True)
    except Exception:
        __die_failure(messages(language, "wrong_values"))


def _start_api(api_host, api_port, api_debug_mode, api_access_key,
               api_client_white_list, api_client_white_list_ips,
               api_access_log, api_access_log_filename, api_cert,
               api_cert_key, language):
    """
    entry point to run the API through the flask

    Args:
        api_host: host/IP to bind address
        api_port: bind port
        api_debug_mode: debug mode
        api_access_key: API access key
        api_client_white_list: clients while list flag
        api_client_white_list_ips: clients white list IPs
        api_access_log: access log flag
        api_access_log_filename: access log filename
        api_cert: SSL certificate
        api_cert_key: SSL Private key
        language: language
    """
    # Starting the API
    write_to_api_console(messages(language, "API_key").format(api_access_key))
    p = multiprocessing.Process(target=__process_it,
                                args=(api_host, api_port, api_debug_mode,
                                      api_access_key, api_client_white_list,
                                      api_client_white_list_ips,
                                      api_access_log, api_access_log_filename,
                                      api_cert, api_cert_key, language))
    p.start()
    # Sometimes it's take much time to terminate flask with CTRL+C
    # So It's better to use KeyboardInterrupt to terminate!
    while 1:
        try:
            exitflag = True
            if len(multiprocessing.active_children()) != 0:
                exitflag = False
            time.sleep(0.3)
            if exitflag:
                break
        except KeyboardInterrupt:
            for process in multiprocessing.active_children():
                process.terminate()
            break

    __die_success()
