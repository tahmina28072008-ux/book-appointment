import os
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta, timezone
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import traceback
import uuid
import pytz
from google.cloud import firestore as google_firestore

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize Flask app
app = Flask(__name__)

# --- Firestore Connection Setup ---
db = None
try:
    # On Cloud Run, credentials are automatically provided by the environment.
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)
    logging.info("Firestore connected using Cloud Run environment credentials.")
    db = firestore.client()
except ValueError:
    # If running locally, you'll need a service account JSON file.
    # Set the 'GOOGLE_APPLICATION_CREDENTIALS' environment variable to its file path.
    try:
        if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
            cred = credentials.Certificate(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'))
            firebase_admin.initialize_app(cred)
            logging.info("Firestore connected using GOOGLE_APPLICATION_CREDENTIALS.")
            db = firestore.client()
        else:
            logging.warning("No GOOGLE_APPLICATION_CREDENTIALS found. Running in mock data mode.")
    except Exception as e:
        logging.error(f"Error initializing Firebase: {e}")
        logging.warning("Continuing without database connection. Using mock data.")

# Mock data for demonstration if Firebase is not connected
MOCK_PATIENTS = {
    'Tahmina Akhtar': { # Changed key to full name
        'name': 'Tahmina',
        'surname': 'Akhtar',
        'dateOfBirth': '1992-03-12',
        'insuranceProvider': 'MedStar Health',
        'policyNumber': 'D123456',
        'email': 'tahmina.akhtar2807@gmail.com',
        'bookings': [],
    }
}

# The mock doctor data has been updated to match the user's data structure
MOCK_DOCTORS = {
    'gp-001': {
        'id': 'gp-001',
        'name': 'Dr. Lucy Morgan, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'New York',
        'availability': {
            '2025-09-07': [
                "13:00",
                "14:00"
            ]
        }
    },
    'gp-002': {
        'id': 'gp-002',
        'name': 'Dr. Adam Collins, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'London',
        'availability': {
            '2025-09-07': [],
            '2025-09-08': [
                "09:00",
                "10:00",
                "11:00",
                "12:00",
                "13:00"
            ]
        }
    }
}

# Mock data for insurance cost calculation
INSURANCE_RATES = {
    "MedStar Health": {
        "appointment_cost": 150.00,
        "co_pay": 25.00
    },
    "Blue Cross Blue Shield": {
        "appointment_cost": 180.00,
        "co_pay": 30.00
    },
    "default": {
        "appointment_cost": 200.00,
        "co_pay": 50.00
    }
}

# --- Core Business Logic Functions ---
def calculate_appointment_cost(insurance_provider: str) -> dict:
    """
    Calculates the appointment cost and patient's co-pay based on insurance.
    """
    rates = INSURANCE_RATES.get(insurance_provider, INSURANCE_RATES["default"])
    total_cost = rates["appointment_cost"]
    co_pay = rates["co_pay"]
    
    return {
        "totalCost": total_cost,
        "patientCopay": co_pay,
        "insuranceClaim": total_cost - co_pay
    }


def send_email_to_patient(email: str, booking_details: dict):
    """
    Sends a confirmation email using a secure SMTP connection.
    NOTE: You must configure an application-specific password for your email account
    and store it securely. Do NOT hardcode your main email password here.
    """
    smtp_server = os.environ.get('SMTP_SERVER', "smtp.gmail.com")
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    sender_email = os.environ.get('SMTP_EMAIL', "niljoshna28@gmail.com")
    password = os.environ.get('SMTP_PASSWORD', "nxlcscihekyxcedc")

    if sender_email == "your_email@gmail.com" or password == "your_app_password":
        logging.warning("SMTP configuration not set via environment variables. Cannot send email.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Appointment Confirmation"
    msg["From"] = sender_email
    msg["To"] = email

    # Create the plain-text and HTML versions of your message
    cost_breakdown = booking_details.get('costBreakdown', {})
    total_cost = cost_breakdown.get('totalCost', 0)
    patient_copay = cost_breakdown.get('patientCopay', 0)

    text_content = f"""
    Appointment Confirmation
    Hello,
    Your appointment has been successfully booked with {booking_details.get('doctorName')} on {booking_details.get('appointmentDate')} at {booking_details.get('appointmentTime')}.
    
    Booking Details:
    - Appointment Type: {booking_details.get('bookingType')}
    - Doctor: {booking_details.get('doctorName')}
    - Date: {booking_details.get('appointmentDate')}
    - Time: {booking_details.get('appointmentTime')}
    
    Cost Breakdown:
    - Total Appointment Cost: ${total_cost:.2f}
    - Your Co-pay (After Insurance Claim): ${patient_copay:.2f}
    
    We look forward to seeing you!
    
    Best regards,
    The Clinic Team
    """

    html_content = f"""
    <html>
    <body style="font-family: sans-serif; line-height: 1.6;">
        <h2>Appointment Confirmation</h2>
        <p>Hello,</p>
        <p>Your appointment has been successfully booked with <b>{booking_details.get('doctorName')}</b> on <b>{booking_details.get('appointmentDate')}</b> at <b>{booking_details.get('appointmentTime')}</b>.</p>
        <h3>Booking Details:</h3>
        <ul>
            <li><b>Appointment Type:</b> {booking_details.get('bookingType')}</li>
            <li><b>Doctor:</b> {booking_details.get('doctorName')}</li>
            <li><b>Date:</b> {booking_details.get('appointmentDate')}</li>
            <li><b>Time:</b> {booking_details.get('appointmentTime')}</li>
        </ul>
        <h3>Cost Breakdown:</h3>
        <ul>
            <li><b>Total Appointment Cost:</b> ${total_cost:.2f}</li>
            <li><b>Your Co-pay (After Insurance Claim):</b> ${patient_copay:.2f}</li>
        </ul>
        <p>We look forward to seeing you!</p>
        <p>Best regards,<br>The Clinic Team</p>
    </body>
    </html>
    """

    part1 = MIMEText(text_content, "plain")
    part2 = MIMEText(html_content, "html")
    msg.attach(part1)
    msg.attach(part2)

    try:
        # Create a secure SSL context and a connection
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)
            server.login(sender_email, password)
            server.sendmail(sender_email, email, msg.as_string())
            logging.info(f"Email sent successfully via SMTP to {email}")
    except Exception as e:
        logging.error(f"Failed to send email via SMTP: {e}")


def find_available_doctors(specialty, location, date_str):
    """
    Helper function to search for available doctors and their times.
    This version of the function is updated to handle the user's Firestore data structure,
    where availability is a map of dates to arrays of time strings.
    """
    available_doctors = []
    if db:
        docs_ref = db.collection('doctors')
        docs = docs_ref.where(filter=firestore.FieldFilter('specialty', '==', specialty)).where(filter=firestore.FieldFilter('city', '==', location)).stream()
        for doc in docs:
            doctor_data = doc.to_dict()
            availability_map = doctor_data.get('availability', {})
            # Check if availability for the date is a list
            if date_str in availability_map and isinstance(availability_map[date_str], list):
                # If the list is not empty, add the full doctor data
                if availability_map[date_str]:
                    available_doctors.append(doctor_data)
            else:
                logging.warning(f"Skipping doctor {doctor_data.get('name')} due to invalid availability data for date {date_str}. Expected a list of strings.")
    else: # Mock data fallback
        for doc in MOCK_DOCTORS.values():
            if doc['specialty'] == specialty and doc['city'] == location:
                availability_map = doc.get('availability', {})
                if date_str in availability_map and isinstance(availability_map[date_str], list):
                    if availability_map[date_str]:
                        available_doctors.append(doc)
    return available_doctors


# --- Webhook Endpoints ---
@app.route('/')
def home():
    """Returns a simple message to confirm the service is running."""
    return "Webhook is running successfully!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Dialogflow CX webhook for doctor availability and booking.
    """
    logging.info("--- Webhook Request Received ---")
    request_data = request.get_json()
    logging.info(f"Full Request JSON: {request_data}")
    
    session_info = request_data.get('sessionInfo', {})
    parameters = session_info.get('parameters', {})
    tag = request_data.get('fulfillmentInfo', {}).get('tag')
    
    response_text = "I'm sorry, an error occurred. Please try again."
    
    if tag == 'search_doctors':
        specialty = parameters.get('specialty')
        location_param = parameters.get('location')
        if isinstance(location_param, dict):
            location = location_param.get('city')
        else:
            location = location_param
        
        date_param = parameters.get('date')
        if date_param:
            try:
                # Convert the ISO 8601 date string to a date object
                requested_date_obj = datetime.fromisoformat(date_param.replace('Z', '+00:00')).date()
                date_str = requested_date_obj.strftime('%Y-%m-%d')
            except ValueError:
                response_text = "I couldn't understand the date provided. Please try again."
                return jsonify({'fulfillmentResponse': {'messages': [{'text': {'text': [response_text]}}]}})
        else:
            # If no date is provided, use the current date in UTC to avoid timezone issues
            requested_date_obj = datetime.now(pytz.utc).date()
            date_str = requested_date_obj.strftime('%Y-%m-%d')
            logging.info(f"No date provided. Using current date: {date_str}")
        
        if not specialty or not location:
            response_text = "I'm missing some information. Please provide your preferred specialty and location."
        else:
            try:
                today = datetime.now(pytz.utc).date()
                
                if requested_date_obj < today:
                    response_text = "I can only check for future appointments. Please provide a date that isn't in the past."
                else:
                    # Initial search for the requested date
                    available_doctors = find_available_doctors(specialty, location, date_str)
                    
                    # If no doctors found, search for the next available date
                    if not available_doctors:
                        found_next_date = False
                        for i in range(1, 8):
                            next_date = requested_date_obj + timedelta(days=i)
                            next_date_str = next_date.strftime('%Y-%m-%d')
                            available_doctors = find_available_doctors(specialty, location, next_date_str)
                            if available_doctors:
                                response_text_list = ["I couldn't find any appointments for your requested date. However, I found some for the next available date, which is {}.".format(next_date.strftime('%B %d, %Y'))]
                                found_next_date = True
                                for i, doc in enumerate(available_doctors):
                                    response_text_list.append(f"\n{i+1}. {doc['name']}, {doc['specialty']}")
                                    response_text_list.append(f"    - Available date: {datetime.strptime(next_date_str, '%Y-%m-%d').strftime('%B %d, %Y')}")
                                    response_text_list.append("    - Available times:")
                                    for time in doc['availability'][next_date_str]:
                                        response_text_list.append(f"      - {time}")
                                response_text = "\n".join(response_text_list)
                                break
                    
                        if not found_next_date:
                            response_text = f"I could not find any {specialty} doctors in {location} available on or after {requested_date_obj.strftime('%B %d, %Y')}. Would you like to check a different date or location?"
                    else:
                        response_text_list = []
                        for i, doc in enumerate(available_doctors):
                            response_text_list.append(f"\n{i+1}. {doc['name']}, {doc['specialty']}")
                            response_text_list.append(f"    - Available date: {datetime.strptime(date_str, '%Y-%m-%d').strftime('%B %d, %Y')}")
                            response_text_list.append("    - Available times:")
                            for time in doc['availability'][date_str]:
                                response_text_list.append(f"      - {time}")
                        response_text = "\n".join(response_text_list)
                    
            except Exception as e:
                logging.error(f"Error searching for doctors: {e}")
                logging.error(traceback.format_exc())
                response_text = "I am having trouble looking for doctors right now. Please try again later."
    
    # --- New Fulfillment Tag to Pass Parameters to the Patient Info Collection UI ---
    elif tag == 'collect_patient_info':
        try:
            # Capture the parameters from the user's previous selection
            doctor_name = parameters.get('doctor_name')
            appointment_time = parameters.get('appointment_time')
            # Now correctly pass appointment_date as a custom payload
            appointment_date = parameters.get('appointment_date')

            if not doctor_name or not appointment_time or not appointment_date:
                response_text = "I'm sorry, I seem to have lost the appointment details. Please try selecting a time again."
            else:
                # A custom payload is used to pass data to a rich UI in Dialogflow.
                # The client-side UI for 'CollectPatientInfo' can read this data to pre-populate fields.
                custom_payload = {
                    "doctorName": doctor_name,
                    "appointmentTime": appointment_time,
                    "appointmentDate": appointment_date
                }
                
                # The fulfillment response will contain the custom payload
                # and a generic text prompt for the next step.
                return jsonify({
                    'fulfillmentResponse': {
                        'messages': [
                            {
                                'text': {
                                    'text': ["You're booking with " + doctor_name['original'] + " on " + str(int(appointment_date['year'])) + "/" + str(int(appointment_date['month'])) + "/" + str(int(appointment_date['day'])) + " at " + str(int(appointment_time['hours'])) + ":" + str(int(appointment_time['minutes'])) + ". Let's collect your details.\n" + "Please provide your personal and insurance details to complete the booking."]
                                }
                            },
                            {
                                'payload': {
                                    'customPayload': custom_payload
                                }
                            }
                        ]
                    }
                })
        except Exception as e:
            logging.error(f"Error in collect_patient_info: {e}")
            logging.error(traceback.format_exc())
            response_text = "I am having trouble with the next step. Please try again later."
    
    elif tag == 'ConfirmCost':
        try:
            # Correctly retrieve parameters from the request payload
            # The parameter names now match what Dialogflow sends
            patient_name_param = parameters.get('name')
            dob_param = parameters.get('dateofbirth')
            insurance_provider = parameters.get('insuranceprovider')
            policy_number = parameters.get('policynumber')
            specialty = parameters.get('specialty')
            location = parameters.get('location')
            doctor_name = parameters.get('doctor_name')
            appointment_date_param = parameters.get('appointment_date')
            appointment_time = parameters.get('appointment_time')

            if not all([patient_name_param, dob_param, insurance_provider, policy_number, specialty, location, doctor_name, appointment_date_param, appointment_time]):
                response_text = "I'm missing some information to complete your booking. Please provide all details."
            else:
                cost_details = calculate_appointment_cost(insurance_provider)
                
                # Fixed the mock data lookup to use the full name as the key
                patient_data = MOCK_PATIENTS.get(patient_name_param.get('original'))
                if patient_data:
                    booking_details = {
                        "bookingId": str(uuid.uuid4()),
                        "bookingType": "appointment",
                        "doctorName": doctor_name['original'],
                        "specialty": specialty,
                        "appointmentDate": f"{appointment_date_param['year']}-{appointment_date_param['month']}-{appointment_date_param['day']}",
                        "appointmentTime": f"{appointment_time['hours']}:{appointment_time['minutes']}",
                        "costBreakdown": cost_details,
                        "status": "confirmed"
                    }
                    send_email_to_patient(patient_data['email'], booking_details)
                    
                    response_text = f"Success! Your booking has been confirmed with {doctor_name['original']}. The total cost is ${cost_details['totalCost']:.2f} with a patient co-pay of ${cost_details['patientCopay']:.2f}. An email has been sent to your registered address."
                else:
                    response_text = "I could not find a patient with the provided details to confirm your booking. Please check your information."

        except Exception as e:
            logging.error(f"Error during ConfirmCost process: {e}")
            logging.error(traceback.format_exc())
            response_text = "I am having trouble confirming your booking right now. Please try again later."


    elif tag == 'book_appointment':
        try:
            # Corrected parameter names to match Dialogflow's sessionInfo
            patient_name_param = parameters.get('name')
            dob_param = parameters.get('dateofbirth')
            insurance_provider = parameters.get('insuranceprovider')
            policy_number = parameters.get('policynumber')
            specialty = parameters.get('specialty')
            doctor_name_param = parameters.get('doctor_name')
            appointment_date_param = parameters.get('appointment_date')
            appointment_time_param = parameters.get('appointment_time')

            if not all([patient_name_param, dob_param, insurance_provider, policy_number, specialty, doctor_name_param, appointment_date_param, appointment_time_param]):
                response_text = "I'm missing some information to complete your booking. Please provide all details."
            else:
                appointment_date = f"{appointment_date_param['year']}-{appointment_date_param['month']}-{appointment_date_param['day']}"
                appointment_time = f"{appointment_time_param['hours']}:{appointment_time_param['minutes']}"
                doctor_name = doctor_name_param['original']
                patient_full_name = patient_name_param['original']
                
                # FIX: Split the full name into first and last name to match the Firestore schema
                name_parts = patient_full_name.split(' ', 1)
                first_name = name_parts[0]
                last_name = name_parts[1] if len(name_parts) > 1 else ''

                patient_doc_ref = None
                if db:
                    patients_ref = db.collection('patients')
                    # FIX: Query using both name and surname fields
                    patient_query = patients_ref.where(filter=google_firestore.FieldFilter('name', '==', first_name)).where(filter=google_firestore.FieldFilter('surname', '==', last_name)).limit(1).stream()
                    
                    for doc in patient_query:
                        patient_doc_ref = doc.reference
                        patient_data = doc.to_dict()
                        break
                    
                    if not patient_doc_ref:
                        response_text = "I could not find a patient with the provided details. Please try again."
                    else:
                        doctor_doc_ref = None
                        doctors_ref = db.collection('doctors')
                        doctor_query = doctors_ref.where(filter=google_firestore.FieldFilter('name', '==', doctor_name)).limit(1).stream()
                        for doc in doctor_query:
                            doctor_doc_ref = doc.reference
                            doctor_data = doc.to_dict()
                            break

                        if not doctor_doc_ref:
                            response_text = "The doctor you selected could not be found."
                        else:
                            # Check if the time slot is a string within the availability array
                            availability_list = doctor_data.get('availability', {}).get(appointment_date, [])
                            if appointment_time in availability_list:
                                cost_details = calculate_appointment_cost(insurance_provider)
                                booking_details = {
                                    "bookingId": str(uuid.uuid4()),
                                    "bookingType": "appointment",
                                    "doctorName": doctor_name,
                                    "specialty": specialty,
                                    "appointmentDate": appointment_date,
                                    "appointmentTime": appointment_time,
                                    "costBreakdown": cost_details,
                                    "status": "confirmed",
                                    "createdAt": firestore.SERVER_TIMESTAMP
                                }
                                
                                # Use a transaction to ensure atomic updates
                                @google_firestore.transactional
                                def update_patient_and_doctor_in_transaction(transaction, patient_ref, doctor_ref, booking_info):
                                    snapshot = patient_ref.get(transaction=transaction)
                                    current_bookings = snapshot.to_dict().get('bookings', [])
                                    current_bookings.append(booking_info)
                                    transaction.update(patient_ref, {'bookings': current_bookings})
                                    
                                    doctor_snapshot = doctor_ref.get(transaction=transaction)
                                    doctor_data_trans = doctor_snapshot.to_dict()
                                    availability_list_trans = doctor_data_trans.get('availability', {}).get(appointment_date, [])
                                    
                                    if booking_info['appointmentTime'] in availability_list_trans:
                                        availability_list_trans.remove(booking_info['appointmentTime'])
                                        update_path = f"availability.{appointment_date}"
                                        transaction.update(doctor_ref, {update_path: availability_list_trans})
                                    else:
                                        raise ValueError("Time slot is no longer available.")
                                        
                                transaction = db.transaction()
                                try:
                                    update_patient_and_doctor_in_transaction(transaction, patient_doc_ref, doctor_doc_ref, booking_details)
                                    
                                    patient_email = patient_data.get('email')
                                    if patient_email:
                                        send_email_to_patient(patient_email, booking_details)

                                    response_text = f"Success! Your booking has been confirmed. The total cost is ${cost_details['totalCost']:.2f} with a patient co-pay of ${cost_details['patientCopay']:.2f}. An email has been sent to your registered address."
                                except ValueError as e:
                                    response_text = "I'm sorry, that time slot is no longer available. Please select a different time."
                                except Exception as e:
                                    logging.error(f"Transaction failed: {e}")
                                    logging.error(traceback.format_exc())
                                    response_text = "An error occurred during the booking process. Please try again."
                            else:
                                response_text = "I'm sorry, that time slot is no longer available. Please select a different time."
                else:
                    patient_data = MOCK_PATIENTS.get(patient_full_name)
                    doctor_data = MOCK_DOCTORS.get('gp-002') # Using gp-002 for London as per user's flow
                    if patient_data and doctor_data and appointment_time in doctor_data['availability'].get(appointment_date, []):
                        cost_details = calculate_appointment_cost(insurance_provider)
                        booking_details = {
                                "bookingId": str(uuid.uuid4()),
                                "bookingType": "appointment",
                                "doctorName": doctor_name,
                                "specialty": specialty,
                                "appointmentDate": appointment_date,
                                "appointmentTime": appointment_time,
                                "costBreakdown": cost_details,
                                "status": "confirmed"
                            }
                        
                        doctor_data['availability'][appointment_date].remove(appointment_time)
                        patient_data['bookings'].append(booking_details)
                        send_email_to_patient(patient_data['email'], booking_details)
                        response_text = f"Success! Your booking has been confirmed. An email has been sent to your registered address."
                    else:
                        response_text = "I could not complete the booking. Either the patient or doctor was not found, or the time slot is unavailable."
            
        except Exception as e:
            logging.error(f"Error during booking process: {e}")
            logging.error(traceback.format_exc())
            response_text = "I am having trouble processing your booking right now. Please try again later."

    logging.info(f"Sending response to Dialogflow: {response_text}")
    return jsonify({
        'fulfillmentResponse': {
            'messages': [
                {
                    'text': {
                        'text': [response_text]
                    }
                }
            ]
        }
    })

# --- Application Entry Point ---
if __name__ == '__main__':
    logging.info("Starting application locally...")
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
