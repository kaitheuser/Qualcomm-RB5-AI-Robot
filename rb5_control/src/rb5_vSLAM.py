#!/usr/bin/env python
import rospy
import csv
import numpy as np
import math
import time
from numpy.linalg import multi_dot
from geometry_msgs.msg import Twist
from april_detection.msg import AprilTagDetectionArray
from tf.transformations import euler_from_quaternion
from rb5_visual_servo_control import PIDcontroller, genTwistMsg, coord

# Write CSV file
timestr = time.strftime("%Y%m%d-%H%M")
fh = open('/home/rosws/src/rb5_ros/telemetry_data/'+timestr+'_path.csv', 'w')
writer = csv.writer(fh)

class EKF_vSLAM:
    def __init__(self, var_System_noise, var_Sensor_noise):
        """
        Initialize pose and covariance. 
        
        Parameters
        ----------
        var_System_noise: list
            Variance of the vehicle model/system noise (size 2)
        var_Sensor_noise: list 
            Variance of the sensor noise for r and phi (size 2)
        """
        self.var_System_noise = var_System_noise 
        self.var_Sensor_noise = var_Sensor_noise
        
        # Initialization
        # self.mu = np.zeros((3, 1))                  # Pose of the vehicle and positions of the landmark. (M - number of landmarks), (3 + 2M, 1)
        self.mu = np.array([[0.61,0.61,0.0]]).T      # For generated path
        self.cov = np.zeros((3, 3))                 # Covariance matrix of the state model, which is the uncertainty in the pose/state estimate, (3 + 2M, 3 + 2M)
        self.observed = []                          # List that stores observed Apriltags' id
    
    def predict_EKF(self, twist):
        """
        EKF Prediction Step 
        
        Parameters
        ----------
        twist: numpy.ndarray (3 + 2M, 1)
            Control vector, which includes twist vector (i.e., linear velocities, and angular velocity)
        dt: float
            Timestep
            
        Return
        ----------
        self.mu: numpy.ndarray (3 + 2M, 1)
            Estimated pose of the vehicle and positions of the landmark. (M - number of landmarks), (3 + 2M, 1)
        self.cov: numpy.ndarray (3 + 2M, 3 + 2M)
            Estimated covariance matrix of the state model, which is the uncertainty in the pose/state estimate, (3 + 2M, 3 + 2M)
        """
        vx, vy, w = twist           # Get vehicle linear velocities and angular velocity
        
        # Get the length of the vehicle state vector.
        mu_len = len(self.mu)
        
        # Define the F matrix, which is the autonomous evloution
        F = np.eye(mu_len)
        # Define the G control matrix
        G = np.zeros((mu_len, mu_len))
        G[0, 0], G[1, 1], G[2, 2] = 1.0, 1.0, 1.0
        # Define the u control vector
        u = np.zeros((mu_len, 1))
        u[0,0], u[1,0], u[2,0] = vx, vy, w
        # Define Qt matrix, which is the uncertainty of the model/system noise
        self.Qt = np.zeros((mu_len, mu_len)) 
        self.Qt[0,0], self.Qt[1,1], self.Qt[2,2] = self.var_System_noise[0], self.var_System_noise[0], self.var_System_noise[1]
        
        # Estimate the state
        # self.mu = F @ self.mu + G @ u
        self.mu = multi_dot([F, self.mu]) + multi_dot([G, u])
        # Estimate the covariance
        # self.cov = F @ self.cov @ F.T + self.Qt
        self.cov = multi_dot([F, self.cov, F.T]) + self.Qt
        
        return self.mu, self.cov
        
    def update_EKF(self, landmarks):
        """
        EKF Update Step 
        
        Parameters
        ----------
        landmarks:
            Detected landmarks
            
        Return
        ----------
        self.mu: numpy.ndarray (3 + 2M, 1)
            Updated pose of the vehicle and positions of the landmark. (M - number of landmarks), (3 + 2M, 1)
        self.cov: numpy.ndarray (3 + 2M, 3 + 2M)
            Updated covariance matrix of the state model, which is the uncertainty in the pose/state estimate, (3 + 2M, 3 + 2M)
        """
        
        x, y, theta = self.mu[:3, 0]           # Get estimated vehicle pose
        
        # Get the length of the state vector.
        mu_len = len(self.mu)

        # Define Sensor Noise
        var_r, var_phi = self.var_Sensor_noise
        
        # tag_id, curr_r, curr_z, curr_x
        for posX_landmark, posY_landmark, tagID in landmarks:
            
            r = np.linalg.norm(np.array([posX_landmark, posY_landmark]))
            phi = math.atan2(posX_landmark, posY_landmark)
            
            if tagID not in self.observed:
                self.observed.append(tagID)         # Append to the observed list
                j = self.observed.index(tagID)      # Get the index of the tagID from the observed list
                # Landmark position in world frame
                landmark_x = x + r * math.cos(phi + theta)          
                landmark_y = y + r * math.sin(phi + theta)
                # Vertically stack to mew
                self.mu = np.vstack((self.mu, landmark_x, landmark_y))
                # Get the length of the vehicle state vector.
                mu_len = len(self.mu)
                # Update Covariance size
                self.cov = np.block([[self.cov, np.zeros((mu_len-2,2))],
                                     [np.zeros((2, mu_len-2)), np.diag(np.array([1e6, 1e6]))]])
                
            j = self.observed.index(tagID)      # Get the index of the tagID from the observed list  
            idx = 3 + 2 * j                     # Determine the index of the tagID for the state vector
            
            # To compute d for the ambiguity problem
            # z_temp = np.array([[r * math.cos(phi + theta)], [r * math.sin(phi + theta)]])
            # p_l = np.array([[self.mu[idx][0] - x],[self.mu[idx+1][0] - y]])
            # cov_temp = self.cov[idx:idx+2, idx:idx+2]
            # inv_cov = np.linalg.inv(cov_temp)
            # d = multi_dot([(z_temp - p_l).T, inv_cov, (z_temp - p_l)])[0][0]
            # print(tagID, d)

            # Determine the distance between landmark position and vehicle position [2, 1]
            delta = np.array([[self.mu[idx][0] - x], [self.mu[idx+1][0] - y]])
            # Determine q (scalar)
            q = multi_dot([delta.T, delta])[0][0]

            z_tilde = np.array([[np.sqrt(q)], [math.atan2(delta[1][0], delta[0][0]) - theta]])
            
            # Create Fxj matrix that map from 2 to 2M + 3
            Fxj = np.zeros((5, mu_len))
            Fxj[:3,:3] = np.eye(3)
            Fxj[3, idx], Fxj[4, idx+1] = 1, 1

            # Define Jacobian Matrix for the Sensor Measurement
            J = 1 / q * np.array([
                [-np.sqrt(q)*delta[0][0], -np.sqrt(q)*delta[1][0], 0, np.sqrt(q)*delta[0][0], np.sqrt(q)*delta[1][0]],
                [delta[1][0], -delta[0][0], -q, -delta[1][0], delta[0][0]]                
                ])
            
            # Calculate H, which is the measurement prediction p(z |s ) ie a prediction of where features 
            # in the world are in the sensory frame [2, 3+2M]
            # H = J @ Fxj 
            H = multi_dot([J, Fxj])

            # Define the sensor noise matrix Rt [2, 2]
            Rt = np.diag(np.array([var_r, var_phi]))

            # Calculate the Kalman Gain, K [3+2M, 2]
            # K = self.cov @ H.T @ np.linalg.inv(H @ self.cov @ H.T + Rt)
            K = multi_dot([self.cov, H.T, np.linalg.inv(multi_dot([H, self.cov, H.T]) + Rt)])
        
            # Define sensor measurement, z
            z = np.array([[r], [phi]])
            
            # Calculate measurement error [2L, 1]
            delta_z =  z - z_tilde
            delta_z[1][0] = (delta_z[1][0] + np.pi) % (2 * np.pi) - np.pi
        
            # Update mu
            self.mu = self.mu + multi_dot([K, delta_z])
            # Update cov
            self.cov = multi_dot([(np.eye(mu_len) - multi_dot([K, H])), self.cov])
            
        return self.mu, self.cov


if __name__ == "__main__":

    # Initialize node
    rospy.init_node("vSLAM")
    # Intialize publisher
    pub_twist = rospy.Publisher("/twist", Twist, queue_size=1)
    
    # Define callback for subscriber
    apriltag_detected = False                # April Detected Boolean
    landmarks_Info = []                      # msg.detections
    def apriltag_callback(msg):
        global apriltag_detected
        global landmarks_Info
        if len(msg.detections) == 0:
            apriltag_detected = False
        else:
            apriltag_detected = True
            landmarks_Info = msg.detections
    # Initialize subscriber
    rospy.Subscriber("/apriltag_detection_array", AprilTagDetectionArray, apriltag_callback)

    # Square Path
    # waypoint = np.array([[0.0,0.0,0.0],
    #                      [1.0,0.0,0.0],
    #                      [1.0,0.0,np.pi/2],
    #                      [1.0,1.0,np.pi/2],
    #                      [1.0,1.0,np.pi],
    #                       [0.0,1.0,np.pi],
    #                       [0.0,0.0,-np.pi/2]])
    
    # Octagon Path
    # waypoint = np.array([[0.0, 0.0, 0.0],
    #                      [0.61, 0.0, 0.0],
    #                      [1.22, 0.61, np.pi/2],
    #                      [1.22, 1.22, np.pi/2],
    #                      [0.61, 1.83, np.pi],
    #                      [0.0, 1.83, np.pi],
    #                      [-0.61, 1.22, -np.pi/2],
    #                      [-0.61, 0.61, -np.pi/2],
    #                      [0.0, 0.0, 0.0]])
    
    # Two Square Path
    # waypoint = np.array([[0.0,0.0,0.0],
    #                      [1.0,0.0,np.pi/2],
    #                      [1.0,1.0,np.pi],
    #                      [0.0,1.0,np.pi],
    #                      [0.0,0.0,0.0],
    #                      [1.0,0.0,np.pi/2],
    #                      [1.0,1.0,np.pi],
    #                      [0.0,1.0,np.pi],
    #                      [0.0,0.0,-np.pi/2]])
    
    # Generated A* Path
    # waypoint = np.array([[0.61,0.61,0.0],
    #                      [0.9,0.9,np.pi/2],
    #                      [0.9,2.1,np.pi/2],
    #                      [1.2,2.4,np.pi/2],
    #                      [2.4,2.4,0.0]])

    # Generated Voronoi Path
    waypoint = np.array([[0.61,0.61,0.0],
                         [2.20,0.60,0.0],
                         [2.40,0.80,0.0],
                         [2.40,2.40,np.pi/2]
                         ])

    

    # init pid controller
    scale = 1.0
    #pid = PIDcontroller(0.03*scale, 0.002*scale, 0.00001*scale)
    #pid = PIDcontroller(0.02*scale, 0.005*scale, 0.00001*scale)
    pid = PIDcontroller(0.04*scale, 0.0005*scale, 0.00005*scale)
    
    # init ekf vslam
    # ekf_vSLAM = EKF_vSLAM(var_System_noise=[1e-6, 0.3], var_Sensor_noise=[1e-6, 3.05e-8])
    ekf_vSLAM = EKF_vSLAM(var_System_noise=[0.1, 0.01], var_Sensor_noise=[0.01, 0.01])
    #ekf_vSLAM = EKF_vSLAM(var_System_noise=[1, 1], var_Sensor_noise=[1, 1])

    # init current state
    current_state = np.array([0.61,0.61,0.0])
    # current_state = np.array([0.0, 0.0, 0.0])
    covariance = np.zeros((3,3))
    
    # Initialize telemetry data acquisition
    t0 = time.time()
    time_counter = 0.0
    data = [time_counter] + current_state.tolist()
    writer.writerow(data)
    writer.writerow(covariance.flatten().tolist())

    # in this loop we will go through each way point.
    # once error between the current state and the current way point is small enough, 
    # the current way point will be updated with a new point.
    for wp in waypoint:
        
        print("move to way point", wp)
        # set wp as the target point
        pid.setTarget(wp)
        # calculate the current twist (delta x, delta y, and delta theta)
        update_value = pid.update(current_state)
        vehicle_twist = coord(update_value, current_state)
        # publish the twist
        pub_twist.publish(genTwistMsg(vehicle_twist))
        time.sleep(0.05)
        
        # Predict EKF
        joint_state, _ = ekf_vSLAM.predict_EKF(update_value)
        
        if apriltag_detected:
            
            # Get landmark
            landmarks = []
            # Stores landmark ids
            landmark_ids = []
            for landmark_info in landmarks_Info:
                tag_id = landmark_info.id
                # Only accept unique landmarks to do localization
                if tag_id not in landmark_ids:
                    _, curr_r, _ = euler_from_quaternion(
                        [
                            landmark_info.pose.orientation.w,
                            landmark_info.pose.orientation.x,
                            landmark_info.pose.orientation.y,
                            landmark_info.pose.orientation.z,
                        ])
                    curr_pose = landmark_info.pose.position
                    curr_x, curr_z = -curr_pose.x, curr_pose.z
                    landmarks.append([curr_x, curr_z, tag_id])  
                    landmark_ids.append(tag_id)
                     
            # Update EKF
            joint_state, _ = ekf_vSLAM.update_EKF(landmarks)
            
        # Update the current state
        current_state = np.array([joint_state[0,0],joint_state[1,0],joint_state[2,0]])

        # Record telemetry        
        t1 = time.time()
        if t1 - t0 >= 0.2:
            time_counter += t1 - t0
            data = [time_counter] + joint_state.reshape(-1).tolist() + ekf_vSLAM.observed
            writer.writerow(data)
            writer.writerow(ekf_vSLAM.cov.flatten().tolist())
            t0 = t1
            

        #while(np.linalg.norm(pid.getError(current_state, wp)) > 0.30): # check the error between current state and current way point
        while(np.linalg.norm(pid.getError(current_state, wp)) > 0.13): 
            # calculate the current twist
            update_value = pid.update(current_state)
            vehicle_twist = coord(update_value, current_state)
            # publish the twist
            pub_twist.publish(genTwistMsg(vehicle_twist))
            time.sleep(0.05)
            
            # Predict EKF
            joint_state, _ = ekf_vSLAM.predict_EKF(update_value)
            
            if apriltag_detected:
                
                # Get landmark
                landmarks = []
                for landmark_info in landmarks_Info:
                    tag_id = landmark_info.id
                    _, curr_r, _ = euler_from_quaternion(
                        [
                            landmark_info.pose.orientation.w,
                            landmark_info.pose.orientation.x,
                            landmark_info.pose.orientation.y,
                            landmark_info.pose.orientation.z,
                        ])
                    curr_pose = landmark_info.pose.position
                    curr_x, curr_z = -curr_pose.x, curr_pose.z
                    landmarks.append([curr_x, curr_z, tag_id])  
                        
                # Update EKF
                joint_state, _ = ekf_vSLAM.update_EKF(landmarks)
                
            # Update the current state
            current_state = np.array([joint_state[0,0],joint_state[1,0],joint_state[2,0]])

            # Record telemetry        
            t1 = time.time()
            if t1 - t0 >= 0.2:
                time_counter += t1 - t0
                data = [time_counter] + joint_state.reshape(-1).tolist() + ekf_vSLAM.observed
                writer.writerow(data)
                writer.writerow(ekf_vSLAM.cov.flatten().tolist())
                t0 = t1
                    
    # stop the car and exit
    pub_twist.publish(genTwistMsg(np.array([0.0,0.0,0.0])))
    # Close csv file
    fh.close()