import os
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import traceback
import uuid
import pytz
from google.cloud import firestore as google_firestore

# --- Logging ---
logging.basicConfig(level=logging.INFO)

# --- Flask App ---
app = Flask(__name__)

# --- Firestore Setup ---
db = None
try:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)
    logging.info("Firestore connected using Cloud Run environment credentials.")
    db = firestore.client()
except ValueError:
    try:
        if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
            cred = credentials.Certificate(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'))
            firebase_admin.initialize_app(cred)
            logging.info("Firestore connected using GOOGLE_APPLICATION_CREDENTIALS.")
            db = firestore.client()
        else:
            logging.warning("No FIREBASE credentials found. Running in mock mode.")
    except Exception as e:
        logging.error(f"Error initializing Firestore: {e}")
        logging.warning("Continuing without database connection.")

# --- Mock Data ---
MOCK_PATIENTS = {
    'Tahmina Akhtar': {
        'name': 'Tahmina',
        'surname': 'Akhtar',
        'dateOfBirth': '1992-03-12',
        'insuranceProvider': 'MedStar Health',
        'policyNumber': 'D123456',
        'email': 'tahmina.akhtar2807@gmail.com',
        'bookings': [],
    }
}

MOCK_DOCTORS = {
    'gp-001': {
        'id': 'gp-001',
        'name': 'Dr. Lucy Morgan, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'London',
        'availability': {
            '2025-09-10': ["04:00 PM", "09:00 AM", "10:00 AM"],
            '2025-09-11': ["04:00 PM", "05:00 PM", "09:00 AM", "10:00 AM"],
            '2025-09-12': ["02:00 PM", "04:00 PM", "10:00 AM"]
        }
    },
    'gp-002': {
        'id': 'gp-002',
        'name': 'Dr. Adam Collins, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'London',
        'availability': {
            '2025-09-10': ["05:00 PM", "09:00 AM", "10:00 AM"],
            '2025-09-11': ["09:00 AM", "10:00 AM", "11:00 AM"],
            '2025-09-12': ["02:00 PM", "09:00 AM", "10:00 AM", "11:00 AM"]
        }
    }
}

INSURANCE_RATES = {
    "MedStar Health": {"appointment_cost": 150.00, "co_pay": 25.00},
    "Blue Cross Blue Shield": {"appointment_cost": 180.00, "co_pay": 30.00},
    "default": {"appointment_cost": 200.00, "co_pay": 50.00}
}

# --- Core Functions ---
def calculate_appointment_cost(insurance_provider: str) -> dict:
    rates = INSURANCE_RATES.get(insurance_provider, INSURANCE_RATES["default"])
    return {
        "totalCost": rates["appointment_cost"],
        "patientCopay": rates["co_pay"],
        "insuranceClaim": rates["appointment_cost"] - rates["co_pay"]
    }

def send_email_to_patient(email: str, booking_details: dict):
    smtp_server = os.environ.get('SMTP_SERVER', "smtp.gmail.com")
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    sender_email = os.environ.get('SMTP_EMAIL', "niljoshna28@gmail.com")
    password = os.environ.get('SMTP_PASSWORD', "nxlcscihekyxcedc")

    if sender_email == "your_email@gmail.com" or password == "your_app_password":
        logging.warning("SMTP not configured. Cannot send email.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Appointment Confirmation"
    msg["From"] = sender_email
    msg["To"] = email

    cost_breakdown = booking_details.get('costBreakdown', {})
    text_content = f"""
    Appointment Confirmation
    Hello,
    Your appointment has been successfully booked with {booking_details.get('doctorName')} 
    on {booking_details.get('appointmentDate')} at {booking_details.get('appointmentTime')}.
    Total cost: ${cost_breakdown.get('totalCost', 0)}, Co-pay: ${cost_breakdown.get('patientCopay', 0)}
    """
    html_content = f"""
    <html><body><h2>Appointment Confirmation</h2>
    <p>Your appointment has been successfully booked with <b>{booking_details.get('doctorName')}</b> 
    on <b>{booking_details.get('appointmentDate')}</b> at <b>{booking_details.get('appointmentTime')}</b>.</p>
    <p>Total cost: <b>${cost_breakdown.get('totalCost', 0)}</b>, Co-pay: <b>${cost_breakdown.get('patientCopay', 0)}</b></p>
    </body></html>
    """
    msg.attach(MIMEText(text_content, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)
            server.login(sender_email, password)
            server.sendmail(sender_email, email, msg.as_string())
            logging.info(f"Email sent to {email}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

def find_available_doctors(specialty, location):
    """
    Retrieve doctors with upcoming availability starting from tomorrow
    """
    available_doctors = []
    if db:
        docs_ref = db.collection('doctors')
        docs = docs_ref.where('specialty', '==', specialty.title()) \
                       .where('city', '==', location.title()).stream()
        for doc in docs:
            doctor_data = doc.to_dict()
            available_doctors.append(doctor_data)
    else:
        for doc in MOCK_DOCTORS.values():
            if doc['specialty'].lower() == specialty.lower() and doc['city'].lower() == location.lower():
                available_doctors.append(doc)

    tomorrow = datetime.now(pytz.utc).date() + timedelta(days=1)
    doctors_list = []
    for doc in available_doctors:
        availability = {}
        for date_str, times in sorted(doc.get('availability', {}).items()):
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            if date_obj >= tomorrow and times:
                availability[date_str] = times
        if availability:
            doctors_list.append({
                "name": doc['name'],
                "specialty": doc['specialty'],
                "city": doc['city'],
                "availability": availability
            })

    return doctors_list

# --- Webhook ---
@app.route('/')
def home():
    return "Webhook is running successfully!"

@app.route('/webhook', methods=['POST'])
def webhook():
    logging.info("--- Webhook Request Received ---")
    request_data = request.get_json()
    logging.info(f"Request JSON: {request_data}")

    session_info = request_data.get('sessionInfo', {})
    parameters = session_info.get('parameters', {})
    tag = request_data.get('fulfillmentInfo', {}).get('tag')

    response_text = "I'm sorry, an error occurred. Please try again."

    # --- Search Doctors ---
    if tag == 'search_doctors':
    specialty = parameters.get('specialty')
    location_param = parameters.get('location')
    location = location_param.get('city') if isinstance(location_param, dict) else location_param

    if not specialty or not location:
        response_text = "Please provide both specialty and location."
    else:
        doctors = find_available_doctors(specialty, location)
        if doctors:
            response_text_list = ["I found the following doctors with availability:"]
            custom_payload = {}

            for doc in doctors:
                response_text_list.append(f"\nDoctor: {doc['name']} ({doc['specialty']})")
                # Prepare payload & human-readable text
                custom_payload[doc['name']] = doc['availability']
                for date, times in sorted(doc['availability'].items()):
                    times_str = ', '.join(times)
                    response_text_list.append(f"  {date}: {times_str}")

            response_text = "\n".join(response_text_list)

            return jsonify({
                'fulfillmentResponse': {
                    'messages': [{'text': {'text': [response_text]}}],
                    'mergeBehavior': 'REPLACE',
                    'payload': {'customPayload': custom_payload}
                }
            })
        else:
            response_text = f"No {specialty} doctors available in {location} from tomorrow onward."


    # --- Collect Patient Info ---
    elif tag == 'collect_patient_info':
        try:
            doctor_name = parameters.get('doctor_name')
            appointment_time = parameters.get('appointment_time')
            appointment_date = parameters.get('appointment_date')

            if not doctor_name or not appointment_time or not appointment_date:
                response_text = "Lost appointment details. Please select again."
            else:
                custom_payload = {
                    "doctorName": doctor_name,
                    "appointmentTime": appointment_time,
                    "appointmentDate": appointment_date
                }
                return jsonify({
                    'fulfillmentResponse': {
                        'messages': [
                            {
                                'text': {'text': [f"You're booking with {doctor_name['original']} on {appointment_date['year']}/{appointment_date['month']}/{appointment_date['day']} at {appointment_time['hours']}:{appointment_time['minutes']}. Please provide your personal and insurance details."]}
                            },
                            {'payload': {'customPayload': custom_payload}}
                        ]
                    }
                })
        except Exception as e:
            logging.error(f"Error in collect_patient_info: {e}")
            logging.error(traceback.format_exc())
            response_text = "Error with next step. Please try again."

    # --- Confirm Cost ---
    elif tag == 'ConfirmCost':
        try:
            patient_name_param = parameters.get('name')
            dob_param = parameters.get('dateofbirth')
            insurance_provider = parameters.get('insuranceprovider')
            specialty = parameters.get('specialty')
            location = parameters.get('location')
            doctor_name = parameters.get('doctor_name')
            appointment_date_param = parameters.get('appointment_date')
            appointment_time = parameters.get('appointment_time')

            if not all([patient_name_param, dob_param, insurance_provider, specialty, location, doctor_name, appointment_date_param, appointment_time]):
                response_text = "Missing information to complete booking."
            else:
                cost_details = calculate_appointment_cost(insurance_provider)
                patient_data = MOCK_PATIENTS.get(patient_name_param.get('original'))

                if patient_data:
                    booking_details = {
                        "bookingId": str(uuid.uuid4()),
                        "doctorName": doctor_name['original'],
                        "specialty": specialty,
                        "appointmentDate": f"{appointment_date_param['year']}-{appointment_date_param['month']}-{appointment_date_param['day']}",
                        "appointmentTime": f"{appointment_time['hours']}:{appointment_time['minutes']}",
                        "costBreakdown": cost_details,
                        "status": "confirmed"
                    }
                    send_email_to_patient(patient_data['email'], booking_details)
                    response_text = f"Booking confirmed with {doctor_name['original']}. Total cost: ${cost_details['totalCost']:.2f}, Co-pay: ${cost_details['patientCopay']:.2f}. Email sent."
                else:
                    response_text = "Patient not found. Check details."
        except Exception as e:
            logging.error(f"Error during ConfirmCost: {e}")
            logging.error(traceback.format_exc())
            response_text = "Error confirming booking. Try again."

    # --- Book Appointment ---
    elif tag == 'book_appointment':
        try:
            patient_name_param = parameters.get('name')
            insurance_provider = parameters.get('insuranceprovider')
            specialty = parameters.get('specialty')
            doctor_name_param = parameters.get('doctor_name')
            appointment_date_param = parameters.get('appointment_date')
            appointment_time_param = parameters.get('appointment_time')

            if not all([patient_name_param, insurance_provider, specialty, doctor_name_param, appointment_date_param, appointment_time_param]):
                response_text = "Missing booking information."
            else:
                patient_full_name = patient_name_param['original']
                appointment_date = f"{appointment_date_param['year']}-{appointment_date_param['month']}-{appointment_date_param['day']}"
                appointment_time = f"{appointment_time_param['hours']}:{appointment_time_param['minutes']}"
                doctor_name = doctor_name_param['original']

                patient_data = MOCK_PATIENTS.get(patient_full_name)
                doctor_data = next((doc for doc in MOCK_DOCTORS.values() if doc['name'] == doctor_name), None)

                if patient_data and doctor_data:
                    times = doctor_data['availability'].get(appointment_date, [])
                    if appointment_time in times:
                        times.remove(appointment_time)
                        doctor_data['availability'][appointment_date] = times
                        cost_details = calculate_appointment_cost(insurance_provider)
                        booking_details = {
                            "bookingId": str(uuid.uuid4()),
                            "doctorName": doctor_name,
                            "specialty": specialty,
                            "appointmentDate": appointment_date,
                            "appointmentTime": appointment_time,
                            "costBreakdown": cost_details,
                            "status": "confirmed"
                        }
                        patient_data['bookings'].append(booking_details)
                        send_email_to_patient(patient_data['email'], booking_details)
                        response_text = f"Booking confirmed with {doctor_name} on {appointment_date} at {appointment_time}. Total cost: ${cost_details['totalCost']:.2f}, Co-pay: ${cost_details['patientCopay']:.2f}."
                    else:
                        response_text = "Selected time not available."
                else:
                    response_text = "Patient or doctor not found."
        except Exception as e:
            logging.error(f"Error booking appointment: {e}")
            logging.error(traceback.format_exc())
            response_text = "Error completing booking. Try again."

    logging.info(f"Response: {response_text}")
    return jsonify({'fulfillmentResponse': {'messages': [{'text': {'text': [response_text]}}]}})

if __name__ == '__main__':
    logging.info("Starting app locally...")
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
