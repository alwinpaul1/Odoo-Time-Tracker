import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import argparse
import datetime
import calendar
import requests
from collections import namedtuple
from dateutil.relativedelta import relativedelta
import math

# These variables will be set by the bot when a user requests a report
SESSION_ID = ""  # Will be populated from user credentials in the bot
CSRF_TOKEN = ""  # Will be populated from user credentials in the bot
UID = 0  # Will be populated from user credentials in the bot

EXPECTED_HOURS_BY_DAY = {
    "Mon": 0.0,
    "Tue": 0.0,
    "Wed": 0.0,
    "Thu": 0.0,
    "Fri": 0.0,
    "Sat": 0.0,
    "Sun": 0.0,
}
# Specify your Bundesland here (e.g., "BB" for Brandenburg, "BE" for Berlin)
MY_BUNDESLAND = "BB"  # Brandenburg

ATTENDANCE_FILENAME = "Anwesenheit (hr.attendance).xlsx"
LEAVE_FILENAME = "Abwesenheiten (hr.leave).xlsx"

# Define leave types
WEEKDAY_KEYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
VACATION_LEAVE_TYPES = ["Urlaub"]
SICK_LEAVE_TYPES = ["Krankheit", "Kinderkrankentag", "Elternzeit"]
SPECIAL_LEAVE_TYPES = []

# ICS file link for holidays
HOLIDAYS_ICS_LINK = "https://www.feiertage-deutschland.de/kalender-download/ics/feiertage-deutschland.ics"

Holiday = namedtuple("Holiday", "summary date is_yearly")

# -------------------- Added Half-Day Configuration --------------------
# List of dates considered as half days. Format: 'YYYY-MM-DD'
HALF_DAY_LIST = [
    "2024-12-24",  # Example: Christmas Day (Half Day)
    "2024-12-31",  # Example: New Year's Eve (Half Day)
]
# -----------------------------------------------------------------------

def pull_attendance_leave_lists():
    """
    Downloads the attendance and leave Excel files from the specified web service.
    The files are saved as ATTENDANCE_FILENAME and LEAVE_FILENAME in the current directory.

    Requires SESSION_ID and CSRF_TOKEN to be correctly set.
    """
    # Validate credentials before making API calls
    if not SESSION_ID or not CSRF_TOKEN or not UID:
        raise ValueError("Missing credentials: SESSION_ID, CSRF_TOKEN, and UID must be set")
    
    if len(SESSION_ID) < 20 or not SESSION_ID.isalnum():
        raise ValueError("Invalid SESSION_ID format. Session IDs are typically long alphanumeric strings.")
    
    if len(CSRF_TOKEN) < 20:
        raise ValueError("Invalid CSRF_TOKEN format. CSRF tokens are typically long strings.")
    
    try:
        uid_int = int(UID)
    except ValueError:
        raise ValueError("Invalid UID format. UID must be a number.")
    
    csrf_token = CSRF_TOKEN

    headers = {
        "Host": "perinet.odoo.com",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://perinet.odoo.com/web",
        "Content-Length": "766",
        "Origin": "https://perinet.odoo.com",
        "Connection": "keep-alive",
        "Cookie": f"session_id={SESSION_ID}; cids=1",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "same-origin",
        "TE": "trailers",
        "Content-Type": "multipart/form-data; boundary=BOUNDARY",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }
    payload_attendance = (
        """--BOUNDARY
Content-Disposition: form-data; name="data"

{"model":"hr.attendance","fields":[{"name":"employee_id","label":"Mitarbeiter","store":true,"type":"many2one"},{"name":"check_in","label":"Einchecken","store":true,"type":"datetime"},{"name":"check_out","label":"Auschecken","store":true,"type":"datetime"},{"name":"worked_hours","label":"Arbeitsstunden","store":true,"type":"float"}],"ids":false,"domain":[],"groupby":[],"context":{"lang":"de_DE","tz":"Europe/Berlin","uid":"""
        + f"{UID}"
        + ""","allowed_company_ids":[1],"params":{"cids":1,"menu_id":672,"action":1004,"model":"hr.attendance","view_type":"list"},"create":false},"import_compat":false}
--BOUNDARY"""
        + f"""
Content-Disposition: form-data; name="csrf_token"

{csrf_token}
--BOUNDARY--"""
    )
    payload_leave = (
        """--BOUNDARY
Content-Disposition: form-data; name="data"

{"model":"hr.leave","fields":[{"name":"holiday_status_id","label":"Abwesenheitstyp","store":true,"type":"many2one"},{"name":"name","label":"Beschreibung","store":false,"type":"char"},{"name":"date_from","label":"Startdatum","store":true,"type":"datetime"},{"name":"date_to","label":"Enddatum","store":true,"type":"datetime"},{"name":"duration_display","label":"Angefragte (Tage/Stunden)","store":true,"type":"char"},{"name":"state","label":"Status","store":true,"type":"selection"}],"ids":false,"domain":[["user_id","=","""
        + f"{UID}"
        + """]],"groupby":[],"context":{"lang":"de_DE","tz":"Europe/Berlin","uid":"""
        + f"{UID}"
        + ""","allowed_company_ids":[1],"params":{"cids":1,"menu_id":651,"action":949,"model":"hr.leave","view_type":"list"}},"import_compat":false}
--BOUNDARY"""
        + f"""
Content-Disposition: form-data; name="csrf_token"

{csrf_token}
--BOUNDARY--"""
    )

    r = requests.post(
        "https://perinet.odoo.com/web/export/xlsx",
        headers=headers,
        data=payload_attendance,
    )
    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401 or e.response.status_code == 403:
            raise ValueError("Authentication failed. Your session ID or CSRF token has expired or is invalid. Please update your credentials.")
        else:
            raise ValueError(f"HTTP error when downloading attendance data: {e.response.status_code}. Your session may have expired.")
    
    with open(ATTENDANCE_FILENAME, "wb") as file:
        file.write(r.content)
    print(f"ATTENDANCE FINISHED WITH STATUS {r.status_code}")
    
    r = requests.post(
        "https://perinet.odoo.com/web/export/xlsx", headers=headers, data=payload_leave
    )
    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401 or e.response.status_code == 403:
            raise ValueError("Authentication failed. Your session ID or CSRF token has expired or is invalid. Please update your credentials.")
        else:
            raise ValueError(f"HTTP error when downloading leave data: {e.response.status_code}. Your session may have expired.")
    
    with open(LEAVE_FILENAME, "wb") as file:
        file.write(r.content)
    print(f"LEAVE FINISHED WITH STATUS {r.status_code}")


def load_attendance_data(file_path, start_date, end_date):
    """
    Loads attendance data from an Excel file and filters it within the specified date range.

    Args:
        file_path (str): Path to the attendance Excel file.
        start_date (datetime.date): Start date for filtering.
        end_date (datetime.date): End date for filtering.

    Returns:
        pd.DataFrame: Filtered attendance data.
    """
    try:
        # Try with openpyxl engine first
        print(f"Attempting to read attendance file: {file_path}")
        df = pd.read_excel(file_path, parse_dates=["Einchecken", "Auschecken"], engine='openpyxl')
    except ValueError as e:
        if "Excel file format cannot be determined" in str(e):
            print("Excel format couldn't be determined with openpyxl, trying xlrd engine...")
            try:
                df = pd.read_excel(file_path, parse_dates=["Einchecken", "Auschecken"], engine='xlrd')
            except Exception as e2:
                print(f"Failed with xlrd engine too: {str(e2)}")
                print(
                    "Couldn't read the attendance excel file. Maybe it wasn't downloaded correctly. This can happen if your login and CSRF token are invalid or outdated."
                )
                # Instead of exiting, let's raise a more descriptive error
                raise ValueError("Failed to read Excel file. Your session ID or CSRF token may be invalid or expired. Please update your credentials using the /credentials command.") from e
        elif "File is not a zip file" in str(e) or "Zip file structure" in str(e):
            print("Invalid Excel file format. This typically happens when credentials are expired or invalid.")
            raise ValueError("Your session ID or CSRF token has expired or is invalid. Please update your credentials using the /credentials command.") from e
        else:
            raise

    # Fill NaN values with current time for the 'Auschecken' column
    df["Auschecken"] = df["Auschecken"].fillna(datetime.datetime.now())

    # Select only the desired timespan
    df = df[
        (df["Einchecken"] >= pd.Timestamp(start_date))
        & (df["Einchecken"] <= pd.Timestamp(end_date + datetime.timedelta(days=1)))
    ]
    return df


def get_holidays_in_daterange(bundesland, start_date, end_date):
    """
    Fetches and parses holidays from an ICS file within a specified date range and Bundesland.

    Args:
        bundesland (str): Bundesland abbreviation (e.g., "BB" for Brandenburg).
        start_date (datetime.date): Start date of the range.
        end_date (datetime.date): End date of the range.

    Returns:
        list of Holiday: List of holidays within the date range.
    """
    r = requests.get(HOLIDAYS_ICS_LINK)
    r.raise_for_status()

    holidays = []
    this_holiday = [None, None, False]
    is_fitting_holiday = False

    # Normalize Bundesland
    bundesland_full_names = {
        "BB": "brandenburg",
        "BE": "berlin",
        "BY": "bayern",
        "NW": "nordrhein-westfalen",
        "HE": "hessen",
        "SN": "sachsen",
        "ST": "sachsen-anhalt",
        "TH": "thueringen",
        "HB": "hamburg",
        "MV": "mecklenburg-vorpommern",
        "SH": "schleswig-holstein",
        "NI": "niedersachsen",
        "BW": "baden-wuerttemberg",
        "RP": "rheinland-pfalz",
        "SL": "saarland",
        # Add any other Bundesländer as needed (Future)
    }
    bundesland_normalized = bundesland_full_names.get(bundesland.upper(), bundesland.lower())

    # Define the three-year window: last year, this year, next year
    current_year = end_date.year
    years_to_include = {current_year - 1, current_year, current_year + 1}

    for line in r.text.split("\n"):
        line = line.strip()
        if line.startswith("BEGIN:VEVENT"):
            this_holiday = [None, None, False]
            is_fitting_holiday = False  # Reset for new event
        elif line.startswith("LOCATION:"):
            location = line[len("LOCATION:") :].strip().lower()
            # Check if the holiday applies to the specified Bundesland or all
            if bundesland_normalized in location or "alle bundesländer" in location:
                is_fitting_holiday = True
        elif line.startswith("SUMMARY:"):
            this_holiday[0] = line[len("SUMMARY:") :].strip()
        elif line.startswith("DTSTART"):
            # Handle different DTSTART formats
            if line.startswith("DTSTART;VALUE=DATE:"):
                dateval = line[len("DTSTART;VALUE=DATE:") :].strip()
            else:
                # Handle datetime DTSTART if present
                dateval = line[len("DTSTART:") :].strip()[:8]  # Extract YYYYMMDD
            try:
                year = int(dateval[0:4])
                month = int(dateval[4:6])
                day = int(dateval[6:8])
                this_holiday[1] = datetime.date(year, month, day)
            except ValueError:
                print(f"Invalid date format in DTSTART: {dateval}")
                continue
        elif line.startswith("RRULE:FREQ=YEARLY"):
            this_holiday[2] = True
        elif line.startswith("END:VEVENT"):
            if is_fitting_holiday and this_holiday[1]:
                if this_holiday[2]:
                    # Generate holiday instances for last, current, and next year
                    for year in years_to_include:
                        try:
                            new_exact_date = datetime.date(
                                year, this_holiday[1].month, this_holiday[1].day
                            )
                            holidays.append(
                                Holiday(this_holiday[0], new_exact_date, this_holiday[2])
                            )
                        except ValueError:
                            # Handle invalid dates like February 29th on non-leap years
                            continue
                else:
                    holidays.append(Holiday(*this_holiday))

    # Filter holidays within the date range
    in_daterange = lambda d: start_date <= d.date and d.date <= end_date
    return list(filter(in_daterange, holidays))


def load_leave_data(file_path, start_date, end_date):
    """
    Loads leave data from an Excel file and filters out rejected leaves.

    Args:
        file_path (str): Path to the leave Excel file.
        start_date (datetime.date): Start date for filtering.
        end_date (datetime.date): End date for filtering.

    Returns:
        pd.DataFrame: Filtered leave data excluding rejected leaves.
    """
    try:
        # Try with openpyxl engine first
        print(f"Attempting to read leave file: {file_path}")
        df = pd.read_excel(file_path, parse_dates=["Startdatum", "Enddatum"], engine='openpyxl')
    except ValueError as e:
        if "Excel file format cannot be determined" in str(e):
            print("Excel format couldn't be determined with openpyxl, trying xlrd engine...")
            try:
                df = pd.read_excel(file_path, parse_dates=["Startdatum", "Enddatum"], engine='xlrd')
            except Exception as e2:
                print(f"Failed with xlrd engine too: {str(e2)}")
                print(
                    "Couldn't read the leave excel file. Maybe it wasn't downloaded correctly. This can happen if your login and CSRF token are invalid or outdated."
                )
                # Instead of exiting, let's raise a more descriptive error
                raise ValueError("Failed to read Excel file. Your session ID or CSRF token may be invalid or expired. Please update your credentials using the /credentials command.") from e
        elif "File is not a zip file" in str(e) or "Zip file structure" in str(e):
            print("Invalid Excel file format. This typically happens when credentials are expired or invalid.")
            raise ValueError("Your session ID or CSRF token has expired or is invalid. Please update your credentials using the /credentials command.") from e
        else:
            raise

    if len(df) != 0:
        df["Start_Date"] = df["Startdatum"].dt.date
        df["End_Date"] = df["Enddatum"].dt.date

    return df.loc[df["Status"] != "Abgelehnt"]


def calculate_overtime_undertime(total_expected, total_worked):
    """
    Calculates the difference between total expected work hours and actual worked hours.

    Args:
        total_expected (float): Total expected work hours.
        total_worked (float): Total worked hours.

    Returns:
        tuple: (Status, hours, minutes)
    """
    difference = total_worked - total_expected

    # Special case: if both expected and worked hours are almost zero, show as Undertime
    if abs(total_expected) < 0.01 and abs(total_worked) < 0.01:
        return "Undertime", 0, 0
    
    # Calculate based on the difference between total worked and total expected
    # regardless of which days the work was performed on
    if difference < 0:
        status = "Undertime"
        # For undertime, we keep the difference positive for display
        time_difference = pd.to_timedelta(abs(difference), unit="h")
    else:
        status = "Overtime"
        # For overtime, we also keep the difference positive for display
        time_difference = pd.to_timedelta(abs(difference), unit="h")

    hours, remainder = divmod(time_difference.total_seconds(), 3600)
    minutes = remainder // 60

    return status, int(hours), int(minutes)


def plot_data(
    daily_work_hours,
    expected_daily_work_hours,
    cumulative_work_hours,
    expected_cumulative_work_hours,
    max_expected_daily_work_hours,
    pdf_file_name,
):
    """
    Generates and saves plots visualizing work hours over time.

    Args:
        daily_work_hours (pd.DataFrame): DataFrame containing daily worked, vacation/sick, and special hours.
        expected_daily_work_hours (pd.Series): Expected work hours per day.
        cumulative_work_hours (pd.Series): Cumulative actual work hours.
        expected_cumulative_work_hours (pd.Series): Cumulative expected work hours.
        max_expected_daily_work_hours (float): Maximum expected daily work hours for plot scaling.
        pdf_file_name (str): Base name for the saved PDF file.
    """
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=False)

    EXPECTED_TIME_COLOR = "#DADFE1"
    WORKTIME_COLOR = "#AAAD00"
    SPECIAL_TIME_COLOR = "#1E1A3F"
    VACATION_SICK_COLOR = "#99A4AE"
    HALF_DAY_COLOR = "#FF5733"

    expected_1 = ax1.bar(
        expected_daily_work_hours.index,
        expected_daily_work_hours,
        color=EXPECTED_TIME_COLOR,
        edgecolor=EXPECTED_TIME_COLOR,
        label="Expected Work Time",
    )

    actual_1 = ax1.bar(
        daily_work_hours.index,
        daily_work_hours["Summed_Hours"],
        color=VACATION_SICK_COLOR,
        edgecolor=VACATION_SICK_COLOR,
        label="Vacation, Holiday and Sick Times",
    )
    _ = ax1.bar(
        daily_work_hours.index,
        daily_work_hours.loc[:, ["Worked_Hours", "Special_Hours"]].sum(axis=1),
        color=SPECIAL_TIME_COLOR,
        edgecolor=SPECIAL_TIME_COLOR,
        label="Special Leave Times",
    )
    _ = ax1.bar(
        daily_work_hours.index,
        daily_work_hours["Worked_Hours"],
        color=WORKTIME_COLOR,
        edgecolor=WORKTIME_COLOR,
        label="Actual Work Time",
    )
    _ = ax1.bar(
        daily_work_hours.index,
        daily_work_hours["Half_Day_Hours"],
        color=HALF_DAY_COLOR,
        edgecolor=HALF_DAY_COLOR,
        label="Half Day Hours",
    )

    ax1.set_title("Work Hours Over Time")
    ax1.set_xlabel("Date")
    ax1.set_ylabel("Daily Work Hours")

    date_format = mdates.DateFormatter("%d.%m\n%a")
    ax1.set_xticks(expected_daily_work_hours.index)
    ax1.xaxis.set_major_formatter(date_format)
    ax1.legend(loc="upper left")

    for expected_bar, actual_bar in zip(expected_1, actual_1):
        height_diff = expected_bar.get_height() - actual_bar.get_height()
        if height_diff < 0:
            label = f"+{abs(height_diff):.1f}h"
            ax1.annotate(
                label,
                xy=(
                    actual_bar.get_x() + actual_bar.get_width() / 2,
                    actual_bar.get_height(),
                ),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
            )
        elif height_diff > 0:
            label = f"-{abs(height_diff):.1f}h"
            ax1.annotate(
                label,
                xy=(
                    actual_bar.get_x() + actual_bar.get_width() / 2,
                    actual_bar.get_height(),
                ),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
            )

    ax2.plot(
        cumulative_work_hours.index,
        cumulative_work_hours,
        color="#C2D100",
        label="Cumulative Work Time",
        marker="o",
        alpha=0.5,
    )

    ax2.fill_between(
        cumulative_work_hours.index,
        cumulative_work_hours,
        color="#C2D100",
        alpha=0.3,
        label="Area under Cumulative Work Time",
    )

    expected_cumulative_work_hours_index = pd.date_range(
        start=cumulative_work_hours.index.min(), end=cumulative_work_hours.index.max()
    )
    expected_2 = ax2.plot(
        expected_cumulative_work_hours_index,
        expected_cumulative_work_hours,
        color="#1E1A3F",
        marker="o",
        linestyle="--",
        label="Expected Cumulative Work Time",
    )

    for i in range(len(cumulative_work_hours)):
        height_diff = (
            expected_cumulative_work_hours.iloc[i] - cumulative_work_hours.iloc[i]
        )
        if height_diff < 0:
            label = f"+{abs(height_diff):.1f}h"
            ax2.annotate(
                label,
                xy=(cumulative_work_hours.index[i], cumulative_work_hours.iloc[i]),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
            )
        elif height_diff > 0:
            label = f"-{abs(height_diff):.1f}h"
            ax2.annotate(
                label,
                xy=(
                    expected_cumulative_work_hours_index[i],
                    expected_cumulative_work_hours.iloc[i],
                ),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
            )

    ax2.set_title("Cumulative Work Time vs. Expected Cumulative Work Time")
    ax2.set_xlabel("Date")
    ax2.set_ylabel("Cumulative Work Time")

    ax2.set_xticks(cumulative_work_hours.index)
    ax2.xaxis.set_major_formatter(date_format)
    ax2.legend(loc="upper left")

    ax1.set_ylim(ax1.get_ylim()[0], ax1.get_ylim()[1] * 1.1)
    ax2.set_ylim(ax2.get_ylim()[0], ax2.get_ylim()[1] * 1.1)

    ax1.axhline(
        y=max_expected_daily_work_hours, linewidth=0.5, color="black", linestyle="-"
    )

    plt.tight_layout()
    plt.show()
    fig.savefig(f"{pdf_file_name}.pdf")


def preprocess_data(df, expected_hours_per_day, start_date, end_date):
    """
    Preprocesses attendance data to calculate daily work hours and expected work hours.

    Args:
        df (pd.DataFrame): Raw attendance data.
        expected_hours_per_day (dict): Expected work hours per weekday.
        start_date (datetime.date): Start date for the analysis.
        end_date (datetime.date): End date for the analysis.

    Returns:
        tuple: (daily_work_hours, expected_daily_work_hours)
    """
    all_days = pd.date_range(start=start_date, end=end_date, freq="D")
    dummy_df = pd.DataFrame(index=all_days)
    dummy_df.index = pd.to_datetime(dummy_df.index)
    df_merged = dummy_df.join(df.set_index("Einchecken_Date"), how="left")
    df_merged["Arbeitsstunden"] = df_merged["Arbeitsstunden"].fillna(0)

    df_merged["Arbeitsstunden"] = df_merged.apply(
        lambda row: (
            (row["Auschecken"] - row["Einchecken"]).total_seconds() / 3600
            if pd.isna(row["Arbeitsstunden"]) or row["Arbeitsstunden"] == 0
            else row["Arbeitsstunden"]
        ) if pd.notna(row["Einchecken"]) and pd.notna(row["Auschecken"]) else 0,
        axis=1,
    )

    df_merged["Expected_Work_Time"] = list(
        map(
            lambda dow: expected_hours_per_day[WEEKDAY_KEYS[dow]],
            df_merged.index.dayofweek,
        )
    )

    daily_work_hours = df_merged.groupby(df_merged.index)["Arbeitsstunden"].sum()
    expected_daily_work_hours = df_merged.groupby(df_merged.index)[
        "Expected_Work_Time"
    ].max()

    return daily_work_hours, expected_daily_work_hours


def calculate_hours_minutes(total_hours):
    """
    Converts total hours in float to hours and minutes.

    Args:
        total_hours (float): Total hours.

    Returns:
        tuple: (hours, minutes)
    """
    hours = int(total_hours)
    minutes = int(round((total_hours - hours) * 60))
    return hours, minutes


def main():
    """
    Main function to execute the script. Parses arguments, processes data, and generates reports.
    """
    parser = argparse.ArgumentParser(
        description="Visualize and analyze work attendance data."
    )
    parser.add_argument(
        "-af",
        "--attendance-file",
        dest="attendance_file",
        type=str,
        default=None,
        help="Path to the Excel file containing attendance data. If given, must also give path to the leave file. (Default: None, pull automatically using session id)",
    )
    parser.add_argument(
        "-lf",
        "--leave-file",
        dest="leave_file",
        type=str,
        help="Path to the Excel file containing leave data. If given, must also give path to the attendance file. (Default: None, pull automatically using session id)",
    )
    parser.add_argument(
        "-ot",
        "--previous-overtime",
        dest="previous_overtime",
        type=float,
        help="Overtime/undertime carried over from before the specified time span (default: 0).",
        default=0,
    )
    parser.add_argument(
        "-s",
        "--start",
        dest="start_date",
        type=lambda s: datetime.datetime.strptime(s, "%Y-%m-%d"),
        help="Start date of the worktimes to visualize (if given, must give end date also)",
        default=None,
    )
    parser.add_argument(
        "-e",
        "--end",
        dest="end_date",
        type=lambda s: datetime.datetime.strptime(s, "%Y-%m-%d"),
        help="End date of the worktimes to visualize (if given, must give start date also)",
        default=None,
    )
    parser.add_argument(
        "-W",
        "--week",
        dest="plot_week",
        default=False,
        action="store_true",
        help="If this flag is given, the data of the current week is plotted. Incompatible with -M",
    )
    parser.add_argument(
        "-M",
        "--month",
        dest="plot_month",
        default=False,
        action="store_true",
        help="If this flag is given, the data of the current month is plotted. Incompatible with -W",
    )
    # for History Check, like pass the lf , af and custom to check the hourly accountability for another previous month.
    # Example: python plot_times.py -lf '.\Abwesenheiten (hr.leave).xlsx' -af '.\Anwesenheit (hr.attendance).xlsx' -c 2024-12
    parser.add_argument(
        "-c",
        "--custom",
        dest="custom_month",
        type=str,
        help="Custom month to process in yyyy-mm format (e.g., 2024-12). Incompatible with --week, --month, and --start/--end.",
        default=None,
    )
    args = parser.parse_args()

    if args.custom_month is not None:
        assert not args.plot_week and not args.plot_month and args.start_date is None and args.end_date is None, (
            "The --custom argument cannot be used with --week, --month, or --start/--end arguments."
        )
    else:
        assert (args.start_date is None) == (
            args.end_date is None
        ), "When specifying a custom timespan, both start and end date must be given."
        assert (args.attendance_file is None) == (
            args.leave_file is None
        ), "When using local attendance / leave files, both files need to be specified."
        if args.start_date is not None:
            assert (
                args.start_date < args.end_date
            ), "Start date cannot be after end date!"
        assert not (args.plot_week and args.plot_month), "-W and -M are incompatible flags."
    # Date Range Determination for Custom Mode
    if args.custom_month is not None:
        try:
            year, month = map(int, args.custom_month.split('-'))
            if not (1 <= month <= 12):
                raise ValueError
            start_date = datetime.date(year, month, 1)
            last_day = calendar.monthrange(year, month)[1]
            end_date = datetime.date(year, month, last_day)
            pdf_file_name = f"worktimes-{year}-{month:02d}"
        except ValueError:
            print("Error: The --custom argument must be in the format yyyy-mm (e.g., 2024-12).")
            exit(1)
    elif args.start_date is not None:
        # Handle custom start and end dates
        pdf_file_name = "worktimes"
        start_date = args.start_date.date()
        end_date = args.end_date.date()
    elif args.plot_week:
        # Handle current week
        now = datetime.datetime.now()
        week_number = now.isocalendar().week
        pdf_file_name = f"worktimes-{now.year}-W{week_number}"

        # Start of the week (Monday)
        start_dt = now - datetime.timedelta(days=now.isocalendar().weekday - 1)
        # End of the week (Sunday)
        end_dt = start_dt + datetime.timedelta(days=6)

        start_date = datetime.date(start_dt.year, start_dt.month, start_dt.day)
        end_date = datetime.date(end_dt.year, end_dt.month, end_dt.day)
    elif args.plot_month:
        now = pd.Timestamp.now()
        pdf_file_name = f"worktimes-{now.year}-{now.month}"
        start_date = datetime.date(now.year, now.month, 1)
        last_day_of_month = calendar.monthrange(now.year, now.month)[1]
        end_date = datetime.date(now.year, now.month, last_day_of_month)
    else:
        # Default to current month if no arguments are provided
        now = pd.Timestamp.now()
        pdf_file_name = f"worktimes-{now.year}-{now.month}"
        start_date = datetime.date(now.year, now.month, 1)
        last_day_of_month = calendar.monthrange(now.year, now.month)[1]
        end_date = datetime.date(now.year, now.month, last_day_of_month)

    if not args.attendance_file:
        assert (
            SESSION_ID != "" and CSRF_TOKEN != ""
        ), "No session ID / CSRF Token configured! Automatic pull of worktime data does not work without a session id and CSRF Token"
        pull_attendance_leave_lists()

    attendance_file_path = (
        args.attendance_file
        if args.attendance_file is not None
        else ATTENDANCE_FILENAME
    )
    leave_file_path = args.leave_file if args.leave_file is not None else LEAVE_FILENAME

    df = load_attendance_data(attendance_file_path, start_date, end_date)
    df["Einchecken_Date"] = df["Einchecken"].dt.date
    df["Auschecken_Date"] = df["Auschecken"].dt.date

    daily_work_hours, expected_daily_work_hours = preprocess_data(
        df, EXPECTED_HOURS_BY_DAY, start_date, end_date
    )

    leaves_df = load_leave_data(leave_file_path, start_date, end_date)
    daily_work_hours = daily_work_hours.to_frame(name="Worked_Hours")
    daily_work_hours.loc[
        :, ["Vacation_Sick_Hours", "Special_Hours", "Half_Day_Hours", "Summed_Hours"]
    ] = 0  # Initialize new column 'Half_Day_Hours'

    vacation_sick_dates = set()
    detailed_leaves = []
    for _, row in leaves_df.iterrows():
        if row["Start_Date"] > end_date or row["End_Date"] < start_date:
            continue
        else:
            leave_start_date = max(row["Start_Date"], start_date)
            leave_end_date = min(row["End_Date"], end_date)
            leave_days_range = pd.date_range(
                start=leave_start_date, end=leave_end_date, freq="D"
            )
            if any(
                row["Abwesenheitstyp"].startswith(t)
                for t in (VACATION_LEAVE_TYPES + SICK_LEAVE_TYPES)
            ):
                for date in leave_days_range:
                    vacation_sick_dates.add(date)
                    detailed_leaves.append({
                        "Date": date.date(),
                        "Type": row["Abwesenheitstyp"],
                        "Hours": expected_daily_work_hours.get(date, 0)
                    })
            elif any(
                row["Abwesenheitstyp"].startswith(t) for t in SPECIAL_LEAVE_TYPES
            ):
                for date in leave_days_range:
                    daily_work_hours.loc[date, "Special_Hours"] = expected_daily_work_hours.get(
                        date, 0
                    )
                    detailed_leaves.append({
                        "Date": date.date(),
                        "Type": row["Abwesenheitstyp"],
                        "Hours": expected_daily_work_hours.get(date, 0)
                    })
            else:
                continue

    holidays = get_holidays_in_daterange(MY_BUNDESLAND, start_date, end_date)
    detailed_holidays = []
    for holiday in holidays:
        vacation_sick_dates.add(pd.Timestamp(holiday.date))
        detailed_holidays.append({
            "Date": holiday.date,
            "Type": "Holiday",
            "Hours": expected_daily_work_hours.get(pd.Timestamp(holiday.date), 0)
        })

    for date in vacation_sick_dates:
        daily_work_hours.loc[date, "Vacation_Sick_Hours"] = expected_daily_work_hours.get(
            date, 0
        )

    # -------------------- Handle Half Days --------------------
    half_day_dates = set()
    for date_str in HALF_DAY_LIST:
        try:
            date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            if start_date <= date_obj <= end_date:
                half_day_dates.add(pd.Timestamp(date_obj))
        except ValueError:
            print(f"Invalid date format in HALF_DAY_LIST: {date_str}. Expected 'YYYY-MM-DD'. Skipping this date.")
            continue

    # Add half of the expected work hours to Half_Day_Hours for each half day
    for date in half_day_dates:
        if date in daily_work_hours.index:
            if (
                daily_work_hours.loc[date, "Vacation_Sick_Hours"] == 0 and
                daily_work_hours.loc[date, "Special_Hours"] == 0
            ):
                half_expected = 0.5 * expected_daily_work_hours.loc[date]
                daily_work_hours.loc[date, "Half_Day_Hours"] += half_expected
                detailed_leaves.append({
                    "Date": date.date(),
                    "Type": "Half Day",
                    "Hours": half_expected
                })
            else:
                # Optionally, log that the half day was skipped
                print(f"Half day on {date.date()} was skipped because it's already marked as a leave day.")
    # Recalculate Summed_Hours to include Half_Day_Hours
    daily_work_hours["Summed_Hours"] = daily_work_hours.loc[
        :, ["Worked_Hours", "Vacation_Sick_Hours", "Special_Hours", "Half_Day_Hours"]
    ].sum(axis=1)

    cumulative_work_hours = daily_work_hours.loc[:, "Summed_Hours"].cumsum()
    expected_cumulative_work_hours = expected_daily_work_hours.cumsum()

    sum_vacation_sick_hours = daily_work_hours["Vacation_Sick_Hours"].sum()
    sum_half_hours = daily_work_hours["Half_Day_Hours"].sum()

    # Calculate Adjusted Expected Hours after removing holidays and vacation
    total_expected_hours = expected_cumulative_work_hours.iloc[-1]
    sum_vacation_sick_hours = daily_work_hours["Vacation_Sick_Hours"].sum()
    sum_half_hours = daily_work_hours["Half_Day_Hours"].sum()
    
    # Fix for the missing hour in the final week of the month
    next_month_expected_hours = 0
    # Check if the last day of the month is not a Sunday (end of week)
    if (args.plot_month or args.custom_month) and end_date.weekday() != 6:  # 6 is Sunday
        # Find the last week in the data
        last_week_end = pd.Timestamp(end_date)
        # Calculate days remaining to complete the week
        days_to_sunday = 6 - end_date.weekday()
        last_week_start = last_week_end - pd.Timedelta(days=end_date.weekday())
        
        # Check if this week spans into the next month
        if last_week_end.month == last_week_start.month:
            # Week is contained within the month, no adjustment needed
            pass
        else:
            # Week spans across months, calculate expected hours for the remaining days
            # that would be in the next month
            next_month_start = end_date + datetime.timedelta(days=1)
            for i in range(days_to_sunday):
                next_day = next_month_start + datetime.timedelta(days=i)
                next_day_weekday = next_day.weekday()
                weekday_key = WEEKDAY_KEYS[next_day_weekday]
                next_month_expected_hours += EXPECTED_HOURS_BY_DAY.get(weekday_key, 0)
            
            # Add a note about the adjustment
            print(f"\nNote: Final week spans into next month. Adjusting calculations to include {days_to_sunday} days from next month.")
            print(f"Additional expected hours from next month days: {next_month_expected_hours:.1f}h")
    
    # Apply the adjustment to the total expected hours
    total_expected_hours_adjusted = total_expected_hours + next_month_expected_hours
    sum_expected_after_holidays_vacation = total_expected_hours_adjusted - sum_vacation_sick_hours - sum_half_hours

    # Calculate Difference as Y - X (Total Expected Hours - Total Worked Hours)
    sum_Worked_Hours = round(daily_work_hours["Worked_Hours"].sum(), 3)
    difference = sum_expected_after_holidays_vacation - sum_Worked_Hours  # D = Y - X

    # Convert to hours and minutes
    worked_hours, worked_minutes = calculate_hours_minutes(sum_Worked_Hours)
    expected_hours, expected_minutes = calculate_hours_minutes(total_expected_hours)
    expected_hours_adjusted, expected_minutes_adjusted = calculate_hours_minutes(total_expected_hours_adjusted)
    sum_expected_after_holidays_vacation_hours, sum_expected_after_holidays_vacation_minutes = calculate_hours_minutes(
        sum_expected_after_holidays_vacation
    )
    difference_hours, difference_minutes = calculate_hours_minutes(
        difference
    )

    # Calculate Status and Difference based on adjusted expected hours (after holidays/vacation)
    status, diff_hours, diff_minutes = calculate_overtime_undertime(
        sum_expected_after_holidays_vacation, sum_Worked_Hours
    )

    if args.plot_week:
        print(
            f"\nTotal hours worked this week: {worked_hours} hours and {worked_minutes} minutes of {expected_hours} hours and {expected_minutes} minutes"
        )
        print(
            f"🕒 Actual Hours Worked: {worked_hours}h {worked_minutes}m"
        )
        print(
            f"📆 Expected Hours: {expected_hours}h {expected_minutes}m"
        )
        print(
            f"✓ Total Hours Accounted: {worked_hours}h {worked_minutes}m"
        )
        # Calculate remaining hours
        remaining_hours = max(0, expected_hours - worked_hours)
        remaining_minutes = max(0, expected_minutes - worked_minutes)
        if remaining_minutes < 0:
            remaining_hours -= 1
            remaining_minutes += 60
        print(
            f"⏳ Remaining Hours Needed: {remaining_hours}h {remaining_minutes}m"
        )
    else:
        # Calculate the total summed hours (actual work + vacation/sick + special + half days)
        total_summed_hours = daily_work_hours["Summed_Hours"].sum()
        total_summed_hours_floor = math.floor(total_summed_hours)
        total_summed_minutes = int((total_summed_hours % 1) * 60)
        
        # Use adjusted expected hours if there's an adjustment
        if next_month_expected_hours > 0:
            print(
                f"\nTotal hours Accounted this period (Including Attendance, leaves, Sick, Half Days, Holidays and Vacation Out of Total work days in this month): {total_summed_hours_floor} hours and {total_summed_minutes} minutes of {expected_hours_adjusted} hours and {expected_minutes_adjusted} minutes (adjusted for month boundary)"
            )
        else:
            print(
                f"\nTotal hours Accounted this period (Including Attendance, leaves, Sick, Half Days, Holidays and Vacation Out of Total work days in this month): {total_summed_hours_floor} hours and {total_summed_minutes} minutes of {expected_hours} hours and {expected_minutes} minutes"
            )
        print(
            f"Total work time accounted so far (Attendance Data): {worked_hours} hours and {worked_minutes} minutes"
        )
        print(
            f"Total hours To complete this period (hours left on Actual Work Days[total work days - All holidays and leaves]): {difference_hours} hours and {difference_minutes} minutes of {sum_expected_after_holidays_vacation_hours} hours and {sum_expected_after_holidays_vacation_minutes} minutes after Holidays and vacation"
        )

    print(f"Status: {status}, Difference: {'+' if status == 'Overtime' else ''}{diff_hours} hours and {diff_minutes} minutes")

    if detailed_holidays:
        print("\nList of Holidays:")
        for holiday in detailed_holidays:
            print(f"Date: {holiday['Date']}, Type: {holiday['Type']}, Hours Accounted: {holiday['Hours']}h")
    else:
        print("\nNo Holidays Detected in this period.")

    if detailed_leaves:
        print("\nList of Leaves and Half Days:")
        for leave in detailed_leaves:
            print(f"Date: {leave['Date']}, Type: {leave['Type']}, Hours Accounted: {leave['Hours']}h")
    else:
        print("\nNo Leaves or Half Days Detected in this period.")

    daily_work_hours.index = pd.to_datetime(daily_work_hours.index)
    weekly_work_hours = daily_work_hours["Summed_Hours"].resample("W-SUN").sum()

    print("\nTotal weekly working hours:")
    for week, hours in weekly_work_hours.items():
        weekly_hours, weekly_minutes = calculate_hours_minutes(hours)
        
        # Check if this is the last week of the month and it's incomplete
        week_end_date = week.date()
        week_start_date = (week - pd.Timedelta(days=6)).date()
        
        # If the week spans across month boundaries and we're in month view
        if (args.plot_month or args.custom_month) and week_end_date.month != week_start_date.month:
            # Calculate how many days of this week are in the current month
            days_in_current_month = sum(1 for d in pd.date_range(week_start_date, week_end_date) 
                                      if d.month == end_date.month and d.year == end_date.year)
            
            # Add a note about partial week
            print(
                f"Week ending {week.strftime('%Y-%m-%d')}: {weekly_hours} hours and {weekly_minutes} minutes (Partial week: {days_in_current_month}/7 days in this month)"
            )
        else:
            print(
                f"Week ending {week.strftime('%Y-%m-%d')}: {weekly_hours} hours and {weekly_minutes} minutes"
            )

    plot_data(
        daily_work_hours,
        expected_daily_work_hours,
        cumulative_work_hours,
        expected_cumulative_work_hours,
        expected_daily_work_hours.max(),
        pdf_file_name,
    )


if __name__ == "__main__":
    main()
