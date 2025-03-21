# Odoo Login Automation CLI
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.firefox import GeckoDriverManager
import time
import json
import re
import argparse
import platform
import os
import subprocess


def perform_automation(url, email, password, browser, headless, full_automation):
    driver = None
    print("Starting Odoo Login Automation...")
    
    try:
        if browser == "chrome":
            # Setup Chrome options
            print("Setting up Chrome browser...")
            chrome_options = ChromeOptions()
            chrome_options.add_argument("--start-maximized")
            if headless:
                chrome_options.add_argument("--headless=new")
            
            # Check if running on macOS ARM architecture
            is_mac_arm = platform.system() == 'Darwin' and platform.machine().startswith('arm')
            if is_mac_arm:
                print("Detected macOS ARM architecture")
                # Try to find Chrome browser installed on the system
                chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                if not os.path.exists(chrome_path):
                    # Try to find Chrome using mdfind
                    try:
                        chrome_path = subprocess.check_output(
                            ["mdfind", "kMDItemCFBundleIdentifier == 'com.google.Chrome'"], 
                            text=True
                        ).strip().split("\n")[0]
                        if chrome_path:
                            chrome_path = os.path.join(chrome_path, "Contents/MacOS/Google Chrome")
                    except:
                        print("Warning: Could not find Chrome browser using mdfind")
                
                if os.path.exists(chrome_path):
                    print(f"Using Chrome at: {chrome_path}")
                    chrome_options.binary_location = chrome_path
                
                try:
                    # Try using local Chrome installation without webdriver-manager
                    driver = webdriver.Chrome(options=chrome_options)
                except Exception as e:
                    print(f"First attempt failed: {str(e)}")
                    print("Trying with webdriver-manager...")
                    try:
                        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), 
                                               options=chrome_options)
                    except Exception as e2:
                        print(f"Second attempt failed: {str(e2)}")
                        print("Trying with ChromeDriverManager(os_type='mac_arm64')...")
                        # Try specifying os_type explicitly
                        driver = webdriver.Chrome(
                            service=ChromeService(ChromeDriverManager(os_type="mac_arm64").install()),
                            options=chrome_options
                        )
            else:
                # Initialize Chrome driver for non-ARM macOS or other platforms
                driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), 
                                        options=chrome_options)
        else:
            # Setup Firefox options
            print("Setting up Firefox browser...")
            firefox_options = FirefoxOptions()
            if headless:
                firefox_options.add_argument("--headless")
            
            # Initialize Firefox driver
            driver = webdriver.Firefox(service=FirefoxService(GeckoDriverManager().install()), 
                                      options=firefox_options)
        
        # Navigate to the Odoo login page
        print(f"Navigating to {url}...")
        driver.get(url)
        time.sleep(2)  # Wait for page to load
        
        # Find and fill login fields
        print("Filling login credentials...")
        email_field = driver.find_element(By.ID, "login")
        password_field = driver.find_element(By.ID, "password")
        
        email_field.clear()
        email_field.send_keys(email)
        
        password_field.clear()
        password_field.send_keys(password)
        
        # Click login button
        print("Submitting login...")
        login_button = driver.find_element(By.XPATH, 
                      "//button[@type='submit' and contains(@class, 'btn-primary')]")
        login_button.click()
        
        # Wait for login to complete and dashboard to load
        print("Waiting for dashboard to load...")
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "o_app")))
        
        print("Successfully logged in")
        
        if full_automation:
            # Click on Attendance app
            print("Looking for Attendance app...")
            
            # Try different methods to find the Attendance app
            try:
                # First try using the data-menu-xmlid attribute
                attendance_app = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "[data-menu-xmlid='hr_attendance.menu_hr_attendance_root']")))
            except:
                try:
                    # Then try using the app name text
                    attendance_app = wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "//div[contains(@class, 'o_caption') and text()='Attendances']/..")))
                except:
                    try:
                        # Try by the img alt attribute
                        attendance_app = wait.until(EC.element_to_be_clickable(
                            (By.XPATH, "//img[contains(@alt, 'Attendance')]/parent::a")))
                    except:
                        # Last resort - find by the image source if it has specific content
                        attendance_app = wait.until(EC.element_to_be_clickable(
                            (By.XPATH, "//img[contains(@src, 'attendance')]/parent::a")))
            
            # Click on the Attendance app
            attendance_app.click()
            print("Opened Attendance")
            time.sleep(2)
            
            # Click on Reporting
            print("Looking for Reporting menu...")
            try:
                # Try with data-menu-xmlid
                reporting_menu = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "[data-menu-xmlid='hr_attendance.menu_hr_attendance_reporting']")))
            except:
                try:
                    # Try with text
                    reporting_menu = wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "//a[contains(text(), 'Reporting')]")))
                except:
                    # Try with class and text
                    reporting_menu = wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "//a[contains(@class, 'dropdown-item') and contains(text(), 'Reporting')]")))
            
            reporting_menu.click()
            print("Opened Reporting")
            time.sleep(2)
            
            # Click on Pivot View
            print("Looking for Pivot view button...")
            try:
                # Try with class
                pivot_button = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, ".oi-view-pivot")))
            except:
                try:
                    # Try with icon class
                    pivot_button = wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "//i[contains(@class, 'view-pivot')]")))
                except:
                    # Try with button text
                    pivot_button = wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "//button[contains(@data-tooltip, 'Pivot')]")))
            
            pivot_button.click()
            print("Switched to Pivot view")
            time.sleep(2)
            
            # Click on Download button
            print("Looking for Download button...")
            try:
                # Try with class
                download_button = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, ".o_pivot_download")))
            except:
                try:
                    # Try with icon and class
                    download_button = wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "//button[contains(@class, 'fa-download') and contains(@class, 'o_pivot_download')]")))
                except:
                    # Try with aria-label
                    download_button = wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "//button[@aria-label='Download xlsx']")))
            
            download_button.click()
            print("XLSX file downloading...")
            time.sleep(3)  # Wait for download to start
        
        # Get CSRF token and Session ID
        print("Retrieving session information...")
        
        # Get cookies for session ID
        all_cookies = driver.get_cookies()
        session_id = None
        for cookie in all_cookies:
            if cookie['name'] == 'session_id':
                session_id = cookie['value']
                break
        
        # Get CSRF token from page source or localStorage
        csrf_token = None
        
        # Method 1: From localStorage
        try:
            csrf_token = driver.execute_script("return localStorage.getItem('csrf_token');")
        except:
            pass
        
        # Method 2: From page source
        if not csrf_token:
            try:
                page_source = driver.page_source
                csrf_match = re.search(r"csrf_token\s*:\s*['\"]([^'\"]+)['\"]", page_source)
                if csrf_match:
                    csrf_token = csrf_match.group(1)
            except:
                pass
        
        # Method 3: From sessionStorage
        if not csrf_token:
            try:
                csrf_token = driver.execute_script("return sessionStorage.getItem('csrf_token');")
            except:
                pass
                
        # Method 4: From odoo namespace in window
        if not csrf_token:
            try:
                csrf_token = driver.execute_script("return odoo.csrf_token;")
            except:
                pass
        
        # Display token information
        print("\n---- Session Information ----")
        if session_id:
            print(f"Session ID: {session_id}")
        else:
            print("Session ID: Not found")
            
        if csrf_token:
            print(f"CSRF Token: {csrf_token}")
        else:
            print("CSRF Token: Not found")
        
        print("\nAll Cookies:")
        for cookie in all_cookies:
            print(f"{cookie['name']}: {cookie['value']}")
        
        # Ask user if they want to keep the browser open
        if not headless:
            user_input = input("\nKeep browser open? (y/n): ")
            if user_input.lower() != 'y':
                driver.quit()
                print("Browser closed.")
            else:
                print("Browser remains open. Script complete.")
                print("Press Ctrl+C to exit the script and close the browser.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    driver.quit()
                    print("\nBrowser closed.")
        else:
            driver.quit()
            print("Headless browser closed.")
        
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        if driver:
            driver.quit()
        return False
        
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Odoo Login Automation CLI')
    
    parser.add_argument('--url', type=str, default="https://perinet.odoo.com/web",
                        help='Odoo URL (default: https://perinet.odoo.com/web)')
    parser.add_argument('--email', type=str, required=True,
                        help='Email address for login')
    parser.add_argument('--password', type=str, required=True,
                        help='Password for login')
    parser.add_argument('--browser', type=str, choices=['chrome', 'firefox'], default='chrome',
                        help='Browser to use (default: chrome)')
    parser.add_argument('--no-headless', action='store_true',
                        help='Run in visible browser mode (not headless)')
    parser.add_argument('--no-automation', action='store_true',
                        help='Skip full automation workflow')
    
    args = parser.parse_args()
    
    perform_automation(
        url=args.url,
        email=args.email,
        password=args.password,
        browser=args.browser,
        headless=not args.no_headless,  # Run headless by default
        full_automation=not args.no_automation
    )