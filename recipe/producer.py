import yaml
import json
import datetime
from pathlib import Path
import argparse
import sys
import dateutil

from ooi_harvester.producer import (
    fetch_instrument_streams_list,
    create_request_estimate,
    perform_request,
)
from ooi_harvester.processor.checker import check_in_progress
from ooi_harvester.utils.parser import (
    parse_response_thredds,
    filter_and_parse_datasets,
)
from ooi_harvester.config import (
    CONFIG_PATH_STR,
    RESPONSE_PATH_STR,
    REQUEST_STATUS_PATH_STR,
    COMMIT_MESSAGE_TEMPLATE,
    STATUS_EMOJIS,
)
from ooi_harvester.utils.github import get_status_json, commit, push

HERE = Path(__file__).parent.absolute()
BASE = HERE.parent.absolute()
CONFIG_PATH = BASE.joinpath(CONFIG_PATH_STR)
RESPONSE_PATH = BASE.joinpath(RESPONSE_PATH_STR)
REQUEST_STATUS_PATH = BASE.joinpath(REQUEST_STATUS_PATH_STR)


def parse_args():
    parser = argparse.ArgumentParser(description='Perform data request')
    parser.add_argument(
        '--data-check',
        action='store_true',
        help="Check flag. If activated, only perform data request check",
    )

    return parser.parse_args()


def main(data_check):
    config_json = yaml.load(CONFIG_PATH.open(), Loader=yaml.SafeLoader)
    # To skip when config yaml is invalid
    # TODO: Need to add more checks for other values!
    if ' ' in config_json['instrument']:
        print("Invalid configuration found. Skipping request ...")
        sys.exit(0)

    table_name = "-".join(
        [
            config_json['instrument'],
            config_json['stream']['method'],
            config_json['stream']['name'],
        ]
    )
    instrument_rd = config_json['instrument']
    if data_check:
        print("Checking data ...")
        if not REQUEST_STATUS_PATH.exists() or not RESPONSE_PATH.exists():
            print("Please request data first.")
            sys.exit(0)
        status_json = yaml.load(
            REQUEST_STATUS_PATH.open(), Loader=yaml.SafeLoader
        )
        response = json.load(RESPONSE_PATH.open())

        if 'status_url' in response['result']:
            in_progress = check_in_progress(response['result']['status_url'])
            if not in_progress:
                print("Data available for download")
                status_json["status"] = "success"
                status_json["data_ready"] = True
            else:
                time_since_request = (
                    datetime.datetime.utcnow()
                    - dateutil.parser.parse(response['result']['request_dt'])
                )
                if time_since_request > datetime.timedelta(days=2):
                    catalog_dict = parse_response_thredds(response)
                    filtered_catalog_dict = filter_and_parse_datasets(
                        catalog_dict
                    )
                    if len(filtered_catalog_dict['datasets']) > 0:
                        print(
                            "Data request timeout reached. But nc files are still available."
                        )
                        status_json["status"] = "success"
                        status_json["data_ready"] = True
                    else:
                        print(
                            f"Data request timeout reached. Has been waiting for more than 2 days. ({str(time_since_request)})"
                        )
                        status_json["status"] = "failed"
                        status_json["data_ready"] = False
                else:
                    print(
                        f"Data request time elapsed: {str(time_since_request)}"
                    )
                    sys.exit(0)
        else:
            status_json["status"] = "skip"
            status_json["data_ready"] = False
    else:
        print("Requesting data ...")
        streams_list = fetch_instrument_streams_list(instrument_rd)
        stream_dct = list(
            filter(lambda s: s['table_name'] == table_name, streams_list)
        )[0]
        request_dt = datetime.datetime.utcnow().isoformat()
        estimated_request = create_request_estimate(
            stream_dct=stream_dct,
            refresh=config_json['harvest_options'].get('refresh', False),
            existing_data_path=config_json['harvest_options'].get(
                'path', 's3://ooi-data'
            ),
        )
        if "requestUUID" in estimated_request['estimated']:
            print("Continue to actual request ...")
            request_response = perform_request(
                estimated_request,
                refresh=config_json['harvest_options'].get('refresh', False),
            )

            status_json = get_status_json(table_name, request_dt, 'pending')
        else:
            print("Writing out status to failed ...")
            request_response = estimated_request
            status_json = get_status_json(table_name, request_dt, 'failed')

        RESPONSE_PATH.write_text(json.dumps(request_response))

    REQUEST_STATUS_PATH.write_text(yaml.dump(status_json))

    now = datetime.datetime.utcnow().isoformat()
    # Commit to github
    commit_message = COMMIT_MESSAGE_TEMPLATE(
        status_emoji=STATUS_EMOJIS[status_json['status']],
        status=status_json['status'],
        request_dt=now,
    )
    commit(message=commit_message)
    push()


if __name__ == "__main__":
    args = parse_args()
    main(data_check=args.data_check)
