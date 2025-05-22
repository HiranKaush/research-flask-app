from flask import Blueprint, Response
import cv2  
import mediapipe as mp  
import time  
import math 
import numpy as np
from collections import Counter  
import logging

# Flask Blueprint නිර්මාණය කරනවා. 
# රීසන්: Blueprint භාවිතයෙන් finger counting functionality එකට අදාළ routes organize කරනවා, 
# විශාල Flask app එකකදී code modularize කිරීමට උපකාරී වෙනවා.
finger_counting_bp = Blueprint('finger_counting_bp', __name__)  

# Camera setup කරනවා: resolution 640x480 ලෙස සකසනවා.
# රීසන්: Standard webcam resolution එකක් ලෙස 640x480 භාවිතා කරනවා; 
# processing speed සහ image quality අතර balance එකක් ලබා ගන්නවා.
wCam, hCam = 640, 480  
cap = cv2.VideoCapture(0) 
cap.set(3, wCam)
cap.set(4, hCam) 

# Initial variables define කරනවා: FPS ගණනයට pTime, fingertip, MCP, PIP joint IDs define කරනවා.
# රීසන්: pTime FPS ගණනය කිරීමට යොදා ගන්නවා; 
# tipIds, mcpIds, pipIds MediaPipe hand landmarks හි specific points identify කරන්න යොදා ගන්නවා.
pTime = 0 
tipIds = [4, 8, 12, 16, 20]  # Fingertips (thumb, index, middle, ring, pinky)
mcpIds = [2, 5, 9, 13, 17]   # MCP joints
pipIds = [3, 6, 10, 14, 18]  # PIP joints

# Global variables: finger count, confidence, state, history තබා ගන්නවා.
# රීසන්: Global variables භාවිතයෙන් frame-to-frame data persist කරනවා; 
# HISTORY_SIZE=10 යොදා ගන්නේ temporal smoothing සඳහා finger count history තබා ගැනීමට.
latest_finger_count = 0  
latest_confidence = 0.0 
current_state = 0  # FSM initial state: 0 fingers
HISTORY_SIZE = 10  # Frame buffer size
finger_count_history = []

# HandDetector class එක define කරනවා. 
# රීසන්: MediaPipe hands module භාවිතයෙන් hand detection සහ tracking ලේසි කරනවා; 
# class එක encapsulate කිරීමෙන් code reusability සහ maintainability වැඩි කරනවා.
class handDetector():
    def __init__(self, mode=False, maxHands=2, detectionCon=0.75, trackCon=0.5):
        # Parameters initialize කරනවා: static mode, max hands, detection/tracking confidence.
        # රීසන්: detectionCon=0.75, trackCon=0.5 යොදා ගන්නේ false positives අවම කරමින් 
        # reliable hand detection ලබා ගැනීමට; maxHands=2 යොදා ගන්නේ බොහෝ use cases වලදී hands දෙකක් ප්‍රමාණවත්.
        self.mode = mode 
        self.maxHands = maxHands  
        self.detectionCon = detectionCon  
        self.trackCon = trackCon  
        self.mpHands = mp.solutions.hands 
        self.hands = self.mpHands.Hands(
            static_image_mode=self.mode,  
            max_num_hands=self.maxHands, 
            min_detection_confidence=self.detectionCon,
            min_tracking_confidence=self.trackCon
        )
        self.mpDraw = mp.solutions.drawing_utils 
        self.results = None  

    def findHands(self, img, draw=True):
        # Image එක RGB බවට පරිවර්තනය කරලා hands process කරනවා.
        # රීසන්: MediaPipe RGB images process කරනවා; 
        # draw=True යොදා ගන්නේ debugging සහ visualization සඳහා landmarks overlay කිරීමට.
        imgRGB = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        self.results = self.hands.process(imgRGB)
        if self.results.multi_hand_landmarks and draw: 
            for handLms in self.results.multi_hand_landmarks:
                # Hand landmarks draw කරනවා.
                self.mpDraw.draw_landmarks(img, handLms, self.mpHands.HAND_CONNECTIONS)
        return img  

    def findPosition(self, img, handNo=0, draw=True):
        # Hand landmarks හි coordinates ලබා ගන්නවා.
        # රීසන්: Pixel coordinates ලබා ගන්නේ finger detection logic එකට; 
        # draw=True යොදා ගන්නේ landmark points visualize කිරීමට.
        lmList = []
        if self.results and self.results.multi_hand_landmarks and handNo < len(self.results.multi_hand_landmarks):
            myHand = self.results.multi_hand_landmarks[handNo]
            for id, lm in enumerate(myHand.landmark):
                h, w, c = img.shape
                cx, cy = int(lm.x * w), int(lm.y * h)
                lmList.append([id, cx, cy])
                if draw:
                    cv2.circle(img, (cx, cy), 15, (255, 0, 255), cv2.FILLED)
        return lmList

    def getHandLabel(self, handNo=0):
        # Hand එක left හෝ right ලෙස identify කරනවා.
        # රීසන්: Left/right hand identification යොදා ගන්නේ debugging සහ potential gesture-specific logic වලට.
        if self.results and self.results.multi_handedness and handNo < len(self.results.multi_handedness):
            return self.results.multi_handedness[handNo].classification[0].label  
        return None  

# Hand detector initialize කරනවා.
# රීසන්: Global detector instance එකක් භාවිතයෙන් repeated initialization වළක්වනවා; 
# detectionCon=0.75, maxHands=2 යොදා ගන්නේ robust detection සඳහා.
detector = handDetector(detectionCon=0.75, maxHands=2)    

def preprocess_image(img):
    """අඳුරු තත්වයන්හිදී image එක enhance කරනවා."""
    # රීසන්: CLAHE, contrast adjustment, Gaussian blur යොදා ගන්නේ low-light conditions 
    # වලදී edge detection සහ landmark detection improve කිරීමට.
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    alpha = 2.0
    beta = 30
    adjusted = cv2.convertScaleAbs(enhanced, alpha=alpha, beta=beta)
    blurred = cv2.GaussianBlur(adjusted, (5, 5), 0)
    return blurred, img

def calculate_distance(point1, point2):
    # දෙකක් අතර දුර ගණනය කරනවා.
    # රීසන්: Euclidean distance යොදා ගන්නේ finger positions compare කිරීමට; 
    # simple සහ computationally efficient.
    return math.sqrt((point1[0] - point2[0])**2 + (point1[1] - point2[1])**2)

def calculate_angle(point1, point2, point3):
    # තුන්කෝණයක angle ගණනය කරනවා.
    # රීසන්: Angle calculation යොදා ගන්නේ finger joints හි bend එක measure කරන්න; 
    # robustly identifies whether a finger is extended or bent.
    vector1 = [point1[0] - point2[0], point1[1] - point2[1]]  
    vector2 = [point3[0] - point2[0], point3[1] - point2[1]]  
    dot_product = vector1[0] * vector2[0] + vector1[1] * vector2[1]
    mag1 = math.sqrt(vector1[0]**2 + vector1[1]**2)
    mag2 = math.sqrt(vector2[0]**2 + vector2[1]**2)  
    if mag1 == 0 or mag2 == 0: 
        return 0
    cos_angle = dot_product / (mag1 * mag2) 
    cos_angle = max(min(cos_angle, 1), -1)  
    return math.degrees(math.acos(cos_angle)) 

def calculate_confidence(history):
    # Finger count history එකෙන් confidence ගණනය කරනවා.
    # රීසන්: Confidence score එක යොදා ගන්නේ count stability measure කිරීමට; 
    # Counter භාවිතයෙන් most frequent count හඳුනා ගන්නවා.
    if not history: 
        return 0.0
    counter = Counter(history)  
    most_common_count, frequency = counter.most_common(1)[0]  
    return frequency / len(history)

def edge_based_finger_count(img_gray, lmList):
    """Edge detection භාවිතයෙන් fingers ගණනය කරනවා."""
    # රීසන්: Canny edge detection යොදා ගන්නේ landmark-based counting backup කිරීමට; 
    # robust against lighting changes සහ landmark detection failures.
    edges = cv2.Canny(img_gray, 50, 150)
    
    if len(lmList) == 0:
        # Landmarks නැත්නම් full image එක analyze කරනවා.
        edge_density = np.sum(edges) / (edges.shape[0] * edges.shape[1])
        if edge_density < 10:  # Low edge density = no hand
            return 0
        
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        finger_count = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area > 100:  # Noise filter කරනවා.
                x, y, w, h = cv2.boundingRect(cnt)
                aspect_ratio = w / h if h > 0 else 0
                if 0.2 < aspect_ratio < 1.0 and h > 20:  # Finger-like shape
                    finger_count += 1
        return finger_count
    
    # Landmarks තිබෙනවා නම් hand region එක analyze කරනවා.
    min_x = max(0, min([lm[1] for lm in lmList]) - 30)
    max_x = min(img_gray.shape[1], max([lm[1] for lm in lmList]) + 30)
    min_y = max(0, min([lm[2] for lm in lmList]) - 30)
    max_y = min(img_gray.shape[0], max([lm[2] for lm in lmList]) + 30)
    
    roi = edges[min_y:max_y, min_x:max_x]
    if roi.size == 0:
        return 0
    
    contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    finger_count = 0
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area > 50:  # Minimum area for a finger
            x, y, w, h = cv2.boundingRect(cnt)
            aspect_ratio = w / h if h > 0 else 0
            if 0.2 < aspect_ratio < 1.0 and h > 15:  # Finger-like shape
                finger_count += 1
    
    return finger_count

# Logger setup කරනවා: finger counting process එක log කරන්න.
# රීසන්: Logging යොදා ගන්නේ debugging සහ performance monitoring සඳහා; 
# file සහ stream handlers යොදා ගන්නේ logs file එකකට සහ console එකට output කිරීමට.
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler('finger_counting.log')
stream_handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

def generate_finger_counting_frames():
    # Video frames generate කරනවා finger counting සඳහා.
    # රීසන්: Real-time video streaming යොදා ගන්නේ live finger counting visualization සඳහා; 
    # multipart/x-mixed-replace mimetype භාවිතයෙන් browser එකේ frame-by-frame update කරනවා.
    global pTime, latest_finger_count, latest_confidence, finger_count_history, current_state
    while True:
        success, img = cap.read()  
        if not success: 
            logger.error('Failed to read frame from camera')
            break

        img_gray, img = preprocess_image(img)  
        img = detector.findHands(img)  
        totalFingers = 0
        edge_fingers = 0
        debug_text = [] 
        hand_count = min(2, len(detector.results.multi_hand_landmarks) if detector.results.multi_hand_landmarks else 0) 

        if hand_count > 0:  
            for handNo in range(hand_count):  
                lmList = detector.findPosition(img, handNo=handNo, draw=False)  
                handLabel = detector.getHandLabel(handNo)

                if len(lmList) != 0: 
                    fingers = [0] * 5  
                    wrist = (lmList[0][1], lmList[0][2])
                    index_mcp = (lmList[mcpIds[1]][1], lmList[mcpIds[1]][2])
                    hand_size = calculate_distance(wrist, index_mcp)

                    # Thumb detection: distance සහ threshold භාවිතයෙන් thumb up/down තීරණය කරනවා.
                    # රීසන්: Normalized distance යොදා ගන්නේ hand size variations handle කිරීමට; 
                    # dynamic threshold භාවිතයෙන් accuracy වැඩි කරනවා.
                    thumb_tip = (lmList[tipIds[0]][1], lmList[tipIds[0]][2])
                    thumb_to_index_mcp = calculate_distance(thumb_tip, index_mcp)
                    thumb_to_wrist = calculate_distance(thumb_tip, wrist)
                    normalized_thumb_dist = thumb_to_index_mcp / hand_size
                    thumb_threshold = 0.4 + (hand_size / 200) * 0.2
                    if normalized_thumb_dist > thumb_threshold and thumb_to_wrist > hand_size * 0.6:
                        fingers[0] = 1

                    # Other fingers: angle සහ distance භාවිතයෙන් up/down තීරණය කරනවා.
                    # රීසන්: Angle-based detection යොදා ගන්නේ finger extension robustly identify 
                    # කිරීමට; orientation check යොදා ගන්නේ hand rotation handle කිරීමට.
                    orientation_angle = math.degrees(math.atan2(index_mcp[1] - wrist[1], index_mcp[0] - wrist[0]))
                    is_vertical = abs(orientation_angle) > 45 and abs(orientation_angle) < 135
                    for id in range(1, 5):
                        mcp = (lmList[mcpIds[id]][1], lmList[mcpIds[id]][2])  
                        pip = (lmList[pipIds[id]][1], lmList[pipIds[id]][2]) 
                        tip = (lmList[tipIds[id]][1], lmList[tipIds[id]][2])  
                        angle = calculate_angle(mcp, pip, tip)
                        tip_to_mcp_dist = calculate_distance(tip, mcp)
                        angle_threshold = 130 if is_vertical else 150
                        dist_threshold = hand_size * 0.5
                        if angle > angle_threshold and tip_to_mcp_dist > dist_threshold:
                            fingers[id] = 1

                    # Gesture rejection: open palm detect කරනවා.
                    # රීසන්: False positives වළක්වන්න open palm gestures reject කරනවා.
                    palm_open = all([calculate_distance(wrist, (lmList[tipIds[i]][1], lmList[tipIds[i]][2])) > hand_size * 0.7 for i in range(1, 5)])
                    if palm_open and sum(fingers) == 0:
                        continue 

                    hand_fingers = sum(fingers)
                    totalFingers += hand_fingers 
                    finger_names = ["Thumb", "Index", "Middle", "Ring", "Pinky"] 
                    up_fingers = [finger_names[i] for i in range(5) if fingers[i] == 1]  
                    debug_text.append(f"Hand {handNo+1} ({handLabel}): Landmark: {hand_fingers}")  

                    # Edge-based counting per hand.
                    edge_fingers += edge_based_finger_count(img_gray, lmList)
                    debug_text.append(f"Edge: {edge_fingers}")

            # Combine counts: landmark-based සහ edge-based counts merge කරනවා.
            # රීසන්: Hybrid approach එක යොදා ගන්නේ robustness වැඩි කිරීමට; 
            # confidence භාවිතයෙන් reliable count තෝරනවා.
            final_count = totalFingers
            if abs(totalFingers - edge_fingers) > 2:  # Larger discrepancy threshold
                final_count = edge_fingers if latest_confidence < 0.5 else totalFingers

            finger_count_history.append(final_count)
            if len(finger_count_history) > HISTORY_SIZE:
                finger_count_history.pop(0)

            base_confidence = calculate_confidence(finger_count_history)
            edge_agreement = 1.0 if abs(totalFingers - edge_fingers) <= 2 else 0.5
            smoothed_confidence = base_confidence * 0.6 + edge_agreement * 0.4

            proposed_count = final_count
            count_change = abs(proposed_count - current_state)
            if count_change <= 2 or smoothed_confidence > 0.9:
                current_state = proposed_count

            latest_finger_count = current_state
            latest_confidence = smoothed_confidence

        else:  # No hands detected
            edge_fingers = edge_based_finger_count(img_gray, [])
            if edge_fingers == 0:  # No significant edges = no hand
                totalFingers = 0
                finger_count_history.clear()  
                current_state = 0
                latest_finger_count = 0
                latest_confidence = 0.0
            else:
                totalFingers = edge_fingers
                finger_count_history.append(totalFingers)
                if len(finger_count_history) > HISTORY_SIZE:
                    finger_count_history.pop(0)
                base_confidence = calculate_confidence(finger_count_history)
                latest_finger_count = totalFingers
                latest_confidence = base_confidence * 0.5  # Lower confidence without landmarks
            debug_text.append(f"Edge (No Landmarks): {edge_fingers}")

        cTime = time.time()
        fps = 1 / (cTime - pTime) if (cTime - pTime) != 0 else 0  
        pTime = cTime
        logger.info(f'FPS: {fps}, Fingers: {latest_finger_count}, Confidence: {latest_confidence:.2f}')
        cv2.putText(img, f'Conf: {latest_confidence:.2f}', (20, 110), cv2.FONT_HERSHEY_PLAIN, 2, (0, 255, 255), 2)
        cv2.putText(img, f'Fingers: {latest_finger_count}', (20, 70), cv2.FONT_HERSHEY_PLAIN, 2, (0, 255, 0), 2)
        cv2.putText(img, f'State: {current_state}', (20, 150), cv2.FONT_HERSHEY_PLAIN, 2, (255, 0, 255), 2)
        for i, text in enumerate(debug_text):
            cv2.putText(img, text, (20, 190 + i*40), cv2.FONT_HERSHEY_PLAIN, 2, (0, 255, 255), 2)

        ret, buffer = cv2.imencode('.jpg', img) 
        frame = buffer.tobytes() 
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@finger_counting_bp.route('/feed')
def finger_counting_feed():
    # Video feed route: live video stream එක browser එකට serve කරනවා.
    # රීසන්: Multipart response යොදා ගන්නේ real-time video streaming සඳහා; 
    # browser compatibility එක ලබා ගන්නවා.
    return Response(generate_finger_counting_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@finger_counting_bp.route('/count')
def get_finger_count():
    # Current finger count සහ confidence return කරනවා.
    # රීසන්: API endpoint එක යොදා ගන්නේ client-side applications වලට finger count data ලබා දීමට.
    global latest_finger_count, latest_confidence
    return {"finger_count": latest_finger_count, "confidence": round(latest_confidence, 2)}

def cleanup():
    # Camera resource release කරනවා.
    # රීසන්: Memory leaks වළක්වන්න සහ proper resource management සඳහා.
    cap.release()