#!/usr/bin/env python
import rospy
from copy import deepcopy
import csv
from math import ceil
import matplotlib.pyplot as plt
import numpy as np
import sys
import time
from geometry_msgs.msg import Twist
from april_detection.msg import AprilTagDetectionArray
from tf.transformations import euler_from_quaternion
from rb5_visual_servo_control import PIDcontroller, genTwistMsg, coord
from rb5_vSLAM_Pro import EKF_vSLAM
from path_planner import A_Star, voronoi, Coverage


'''
Map Parameters
--------------
'''
# Define Landmarks/Obstacles' Center Points
dict_wall_lm, dict_obs_lm = {}, {}              # Dictionaries that stores Walls' landmarks, Obstacle landmarks, respectively
# Wall's landmarks' center point (x, y)
dict_wall_lm['lm1'] = [3.05, 2.44]              # TagID 1
dict_wall_lm['lm2'] = [0.61, 3.05]              # TagID 1
dict_wall_lm['lm3'] = [2.44, 0.0]               # TagID 1
dict_wall_lm['lm4'] = [3.05, 0.61]              # TagID 1
dict_wall_lm['lm5'] = [0.61, 0.0]               # TagID 1 
dict_wall_lm['lm6'] = [0.0, 0.61]               # TagID 2
dict_wall_lm['lm7'] = [0.0, 2.44]               # TagID 2
dict_wall_lm['lm8'] = [2.44, 3.05]              # TagID 1
dict_wall_lm['lm9'] = [3.05, 1.525]             # TagID 2
dict_wall_lm['lm10'] = [1.525, 3.05]            # TagID 2
dict_wall_lm['lm11'] = [1.525, 0.0]             # TagID 2
dict_wall_lm['lm12'] = [0.0, 1.525]             # TagID 1

# RB5 Start Position (x, y)
rb5_start = [0.61, 0.61]                        # For A* and Voronoi only
rb5_goal = [2.44, 2.44]                         # For A* and Voronoi only

# Map Configuration
safety_Dist = 0.61                              # Safety distance between planned path and wall
lane_Width = 0.61                               # Robot Size Diameter 0.2m
cell_size = 0.1                                 # Size of the cell 0.1m x 0.1m
rb5_clearance = 0.2                             # Robot Size Diameter 0.2m
goal_tol = 1                                    # Goal Tolerance that considered waypoint is reached, 1 unit cell
verbose = True                                  # True/False - Visualize Path Planner


'''
Path Planner Settings
---------------------
'''
# path_planner = 'A*'         # A* OR Voronoi OR Coverage
# path_planner = 'Voronoi'    # A* OR Voronoi OR Coverage
path_planner = 'Coverage'     # A* OR Voronoi OR Coverage


'''
User-defined/Helper Functions
-----------------------------
'''

# Get height, width of the map
height, width = 0, 0
for _, value in dict_wall_lm.items():
    x, y = value
    width = max(width, x)
    height = max(height, y)
arr_h, arr_w = int(height / cell_size) + 1, int(width / cell_size) + 1

# Build the initial map
map = np.zeros((arr_h, arr_w))

def ground_position_transform(ground_pos):
    '''
    Transform from ground position to map position.
    '''
    x, y = ground_pos
    return (height - y, x)

def array_position_transform(map_pos):
    '''
    Transform from map position to array position/index.
    '''
    x, y = map_pos
    row, col = int(x / cell_size), int(y / cell_size)
    return (row, col)

def ground_to_array_transform(ground_pos):
    '''
    Transform from ground position to array index.
    '''
    return array_position_transform(
        ground_position_transform(
            ground_pos
        )
    )
    
def array_to_ground_transform(arr_pos):
    '''
    Transform array index to ground position.
    '''
    x, y = arr_pos
    return [y * cell_size, 3 - x * cell_size]

def grid_to_ground_transform(arr_pos):
    '''
    Transform grid frame to ground position.
    '''
    x, y = arr_pos
    return [x * cell_size, (map.shape[0] - y - 1) * cell_size]

def can_add_obs(arr_pos):
    '''
    Checking if arr_pos can be treated as an obstacle.
    '''
    x, y = arr_pos
    return 0 <= x < arr_h and 0 <= y < arr_w

def add_obs(min_x, min_y, max_x, max_y):
    '''
    Add obstacle area to the mapping array.
    '''
    clearance = int(ceil(rb5_clearance / cell_size))
    for x in range(min_x - 1 - clearance, max_x + 2 + clearance):
        for y in range(min_y - 1 - clearance, max_y + 2 + clearance):
            if can_add_obs(arr_pos = (x, y)):
                map[x, y] = 1
                

def add_landmarks(obs):
    '''
    Visualize Landmarks
    '''
    for i in obs:
        map[i[0], i[1]] = 5

# Add walls
for i in range(int(ceil(rb5_clearance / cell_size))):
    map[:, i] = map[:, -1 - i] = map[i, :] = map[-1 - i, :] = 1

# Visualize the landmark location on the wall
for _, value in dict_wall_lm.items():
    x, y = value
    map[int(x/cell_size), int(y/cell_size)] = 5
    
# Show the map
if verbose:
    plt.imshow(map)
    plt.ylabel('Height Pixel Coordinate')
    plt.xlabel('Width Pixel Coordinate')
    plt.show()
                
def generate_waypoints(path):
    '''
    Generate waypoints file from generated path.
    '''
    arr_track = list(arr_start)
    prev_action = path.pop(0)
    arr_track[0] += prev_action[0]
    arr_track[1] += prev_action[1]
    waypoints = []

    while path:
        curr_action = path.pop(0)
        delta_x, delta_y = curr_action
        x, y = arr_track
        if curr_action != prev_action:
            waypoints.append(array_to_ground_transform([x, y]))
        arr_track = [x + delta_x, y + delta_y]
        prev_action = curr_action

    waypoints.append(array_to_ground_transform(arr_goal))
    return waypoints

'''
Define and Build Initial Map
----------------------------
'''
arr_start = ground_to_array_transform(rb5_start)
arr_goal = ground_to_array_transform(rb5_goal)

# Write CSV file
timestr = time.strftime("%Y%m%d-%H%M")
fh = open('/home/rosws/src/rb5_ros/telemetry_data/'+timestr+'_path.csv', 'w')
writer = csv.writer(fh)


if __name__ == "__main__":

    # Initialize node
    rospy.init_node("roomba_OS")
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
    
    '''
    Fire up path planner
    '''
    if path_planner == 'A*':
        
        # Create A* path planner object
        a_star = A_Star(
            start = arr_start,
            goal = arr_goal,
            tol = goal_tol,
            map = map,
        )
        
        # Plan path
        a_star.plan_path()
        
        # Show the path
        if verbose:
            map_disp = deepcopy(map)
            disp_start = deepcopy(arr_start)
            map_disp[arr_start[0], arr_start[1]] = 3
            for x, y in a_star.path:
                x_prev, y_prev = disp_start
                disp_start = [x_prev + x, y_prev + y]
                map_disp[x_prev + x, y_prev + y] = 2
            map_disp[arr_goal[0], arr_goal[1]] = 4

            plt.imshow(map_disp)
            plt.title('Path Generated by A* Path Planning Algorithm')
            plt.ylabel('Height Pixel Coordinate')
            plt.xlabel('Width Pixel Coordinate')
            plt.show()
            
        # Generate waypoints
        arr_path = a_star.path
        aStar_waypoints = generate_waypoints(deepcopy(arr_path))
        if verbose:
            print ('[MESSAGE] Printing waypoints generated by A*...')
            print (np.array(aStar_waypoints))
        
        # Define waypoints
        waypoint = np.array(aStar_waypoints)
        waypoint = np.vstack((np.array(rb5_start), waypoint))
        thetas = np.array([[0.0],[np.pi/2],[np.pi/2],[np.pi/2],[0.0]])
        waypoint = np.hstack((waypoint, thetas))
        
        # Save path as csv
        np.savetxt('/home/rosws/src/rb5_ros/waypoints/a_star_waypoints.csv', np.array(waypoint), delimiter=',')
        
    elif path_planner == 'Voronoi':
        
        # Create Voronoi path planner object
        voro = voronoi(start=arr_start, goal=arr_goal, tol=goal_tol, map=map, verbose=verbose)
        
        # Plan path
        voro_path = voro.plan_path()
        
        # Generate waypoints
        voro_waypoints = generate_waypoints(deepcopy(voro_path))
        
        # Show the path
        if verbose:
            map_disp = deepcopy(map)
            disp_start = deepcopy(arr_start)
            map_disp[arr_start[0], arr_start[1]] = 3
            for x, y in voro_path:
                x_prev, y_prev = disp_start
                disp_start = [x_prev + x, y_prev + y]
                map_disp[x_prev + x, y_prev + y] = 2
            map_disp[arr_goal[0], arr_goal[1]] = 4

            plt.imshow(map_disp)
            plt.title('Path Generated by Voronoi Path Planning Algorithm')
            plt.ylabel('Height Pixel Coordinate')
            plt.xlabel('Width Pixel Coordinate')
            plt.show()

            print ('[MESSAGE] Printing waypoints generated by Voronoi')
            print (np.array(voro_waypoints))
        
        # Define waypoints
        waypoint = np.array(voro_waypoints)
        waypoint = np.vstack((np.array(rb5_start), waypoint))
        thetas = np.array([[0.0],[0.0],[0.0],[np.pi/2]])
        waypoint = np.hstack((waypoint, thetas))
        
        # Save path as csv
        np.savetxt('/home/rosws/src/rb5_ros/waypoints/voronoi_waypoints.csv', np.array(waypoint), delimiter=',')
        
    elif path_planner == 'Coverage':
        
        # Create coverage path planner object
        coverage = Coverage(
            map = map,                                         # 2D Map
            cell_size = cell_size,                             # Cell Size
            safety_Dist = safety_Dist,                         # Safety distance between planned path and wall
            lane_Width = lane_Width,                           # Distance between lanes
            verbose = verbose                                  # Visual Display
        )
        
        # Plan path
        coverage_path = coverage.plan_path()

        # Show the path
        if verbose:
            map_disp = deepcopy(map)
            for id, wp in enumerate(coverage.path):
                x, y = wp
                if id == 0:
                    map_disp[y, x] = 3
                    robot_pos = (x, y)
                elif id < len(coverage.path):
                    while (x - robot_pos[0]) > 0:
                        x_pos = robot_pos[0] + 1
                        robot_pos = (x_pos, robot_pos[1])
                        map_disp[robot_pos[1], x_pos] = 2
                    while (y - robot_pos[1]) < 0:
                        y_pos = robot_pos[1] - 1
                        robot_pos = (robot_pos[0], y_pos)
                        map_disp[y_pos, robot_pos[0]] = 2
                    while (x - robot_pos[0]) < 0:
                        x_pos = robot_pos[0] - 1
                        robot_pos = (x_pos, robot_pos[1]) 
                        map_disp[robot_pos[1], x_pos] = 2
                    if id == len(coverage.path)-1:
                        map_disp[y, x] = 4

            plt.imshow(map_disp)
            plt.title('Path Generated by Coverage Path Planning Algorithm')
            plt.ylabel('Height Pixel Coordinate')
            plt.xlabel('Width Pixel Coordinate')
            plt.show()

        # Generate waypoints
        coverage_waypoints = []
        print(coverage_path)

        while coverage_path:
            x, y = coverage_path.pop(0)
            coverage_waypoints.append(grid_to_ground_transform([x, y]))
        print ('[MESSAGE] Printing waypoints generated by Coverage')
        print (np.array(coverage_waypoints))
        
        # Define waypoints
        waypoint = np.array(coverage_waypoints)
        thetas = np.zeros((waypoint.shape[0],1))
        for id in range(0, waypoint.shape[0]):
            if id in list(range(0, waypoint.shape[0],4)):
                thetas[id, 0] = 0.0
            elif id in list(range(1, waypoint.shape[0],4)) or id in list(range(3, waypoint.shape[0],4)):
                thetas[id, 0] = np.pi/2
            elif id in list(range(2, waypoint.shape[0],4)):
                thetas[id, 0] = np.pi
    
        waypoint = np.hstack((waypoint, thetas))

        # waypoint = np.array([[0.6,0.6,0.0],
        #                  [2.4,0.6,0.0],
        #                  [2.4,1.2, -np.pi],
        #                  [0.6,1.2,-np.pi],
        #                  [0.6,1.8,0.0],
        #                  [2.4,1.8,0.0],
        #                  [2.4,2.4,-np.pi],
        #                  [0.6,2.4,-np.pi]])
        
        # Save path as csv
        np.savetxt('/home/rosws/src/rb5_ros/waypoints/coverage_waypoints.csv', np.array(waypoint), delimiter=',')
        
    else:
        
        # Default random waypoints
        waypoint = np.array([[0.0,0.0,0.0],
                             [1.0,0.0,0.0],
                             [1.0,0.0,np.pi/2],
                             [1.0,1.0,np.pi/2],
                             [1.0,1.0,np.pi],
                             [0.0,1.0,np.pi],
                             [0.0,0.0,-np.pi/2]])

    
    # init pid controller
    scale = 1.0
    pid = PIDcontroller(0.04*scale, 0.0005*scale, 0.00005*scale)
    
    # init ekf vslam
    # ekf_vSLAM = EKF_vSLAM(var_System_noise=[0.1, 0.01], var_Sensor_noise=[0.01, 0.01], sensor_Error=0.43)
    ekf_vSLAM = EKF_vSLAM(var_System_noise=[0.1, 0.01], var_Sensor_noise=[0.01, 0.01], sensor_Error=0.50)

    # init current state
    current_state = np.array([0.61,0.61,0.0])
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
            

        # check the error between current state and current way point
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
    
