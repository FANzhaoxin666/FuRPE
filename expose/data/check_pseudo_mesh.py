# -*- coding: utf-8 -*-
import os.path as osp
import os
import time

import torch
import torch.utils.data as dutils
import numpy as np
import torch.nn.functional as F
from smplx import build_layer as build_body_model
import smplx
from fvcore.common.config import CfgNode as CN
from loguru import logger
from tqdm import tqdm
import pickle
import open3d as o3d
Mesh = o3d.geometry.TriangleMesh
Vector3d = o3d.utility.Vector3dVector
Vector3i = o3d.utility.Vector3iVector

def batch_rodrigues(rot_vecs, epsilon=1e-8):
    ''' Calculates the rotation matrices for a batch of rotation vectors
        Parameters:
            rot_vecs: torch.tensor Nx3, array of N axis-angle vectors
        Returns:
            R: torch.tensor Nx3x3, The rotation matrices for the given axis-angle parameters
    '''
    batch_size = rot_vecs.shape[0]
    device = rot_vecs.device
    dtype = rot_vecs.dtype

    angle = torch.norm(rot_vecs + epsilon, dim=1, keepdim=True, p=2)
    rot_dir = rot_vecs / angle

    cos = torch.unsqueeze(torch.cos(angle), dim=1)
    sin = torch.unsqueeze(torch.sin(angle), dim=1)

    # Bx1 arrays
    rx, ry, rz = torch.split(rot_dir, 1, dim=1)
    K = torch.zeros((batch_size, 3, 3), dtype=dtype, device=device)

    zeros = torch.zeros((batch_size, 1), dtype=dtype, device=device)
    K = torch.cat([zeros, -rz, ry, rz, zeros, -rx, -ry, rx, zeros], dim=1) \
        .view((batch_size, 3, 3))

    ident = torch.eye(3, dtype=dtype, device=device).unsqueeze(dim=0)
    rot_mat = ident + sin * K + (1 - cos) * torch.bmm(K, K)
    return rot_mat



def batch_rot2aa(Rs, epsilon=1e-7):
    #Rs is B x 3 x 3
    cos = 0.5 * (torch.einsum('bii->b', [Rs]) - 1)
    cos = torch.clamp(cos, -1 + epsilon, 1 - epsilon)

    theta = torch.acos(cos)

    m21 = Rs[:, 2, 1] - Rs[:, 1, 2]
    m02 = Rs[:, 0, 2] - Rs[:, 2, 0]
    m10 = Rs[:, 1, 0] - Rs[:, 0, 1]
    denom = torch.sqrt(m21 * m21 + m02 * m02 + m10 * m10 + epsilon)

    axis0 = torch.where(torch.abs(theta) < 0.00001, m21, m21 / denom)
    axis1 = torch.where(torch.abs(theta) < 0.00001, m02, m02 / denom)
    axis2 = torch.where(torch.abs(theta) < 0.00001, m10, m10 / denom)

    return theta.unsqueeze(1) * torch.stack([axis0, axis1, axis2], 1)

def extract_hand_output(output, hand_type, hand_info, use_cuda=False):
    assert hand_type in ['left', 'right']

    if hand_type == 'left':
        wrist_idx, hand_start_idx, middle_finger_idx = 20, 25, 28
    else:
        wrist_idx, hand_start_idx, middle_finger_idx = 21, 40, 43

    vertices = output.vertices
    joints = output.joints
    vertices_shift = vertices - joints[:, hand_start_idx:hand_start_idx+1, :]

    hand_verts_idx = torch.Tensor(hand_info[f'{hand_type}_hand_verts_idx']).long()
    if use_cuda:
        hand_verts_idx = hand_verts_idx.cuda()

    hand_verts = vertices[:, hand_verts_idx, :]
    hand_verts_shift = hand_verts - joints[:, hand_start_idx:hand_start_idx+1, :]

   # Hand joints
    if hand_type == 'left':
        hand_idxs =  [20] + list(range(25,40)) + list(range(66, 71)) # 20 for left wrist. 20 finger joints
    else:
        hand_idxs = [21] + list(range(40,55)) + list(range(71, 76)) # 21 for right wrist. 20 finger joints
    smplx_hand_to_panoptic = [0, 13,14,15,16, 1,2,3,17, 4,5,6,18, 10,11,12,19, 7,8,9,20] 
    hand_joints = joints[:, hand_idxs, :][:, smplx_hand_to_panoptic, :]
    hand_joints_shift = hand_joints - joints[:, hand_start_idx:hand_start_idx+1, :]

    output = dict(
        wrist_idx = wrist_idx,
        hand_start_idx = hand_start_idx,
        middle_finger_idx = middle_finger_idx,
        vertices_shift = vertices_shift,
        hand_vertices = hand_verts,
        hand_vertices_shift = hand_verts_shift,
        hand_joints = hand_joints,
        hand_joints_shift = hand_joints_shift
    )
    return output

def load_pkl(pkl_file, res_list=None):
    assert pkl_file.endswith(".pkl")
    with open(pkl_file, 'rb') as in_f:
        try:
            data = pickle.load(in_f)
        except UnicodeDecodeError:
            in_f.seek(0)
            data = pickle.load(in_f, encoding='latin1')
    return data

if __name__ == '__main__':
    #device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
    device = torch.device('cpu')    
    #*************
    dset='curated_fits'#'coco2017'#'3dpw'
    use_frank_smplx=True
    #*************
    # hand_info_file = osp.join('/data/panyuqing/frankmocap/extra_data', 'hand_module/SMPLX_HAND_INFO.pkl')
    # hand_info = load_pkl(hand_info_file)

    #save_crop_path = '/data/panyuqing/experts/'#res
    #save_param_path = '/data/panyuqing/expose_experts/data/params3d_v'
    save_param_path = '/data/panyuqing/expose_experts/data/curated_fits'

    if dset=='coco2017':
        S_num='train'
        add_path = '/data/panyuqing/expose_experts/add_data/coco2017/'+S_num+'/images'
    elif dset=='mpi':
        S_num='S3_Seq2'
        add_path = '/data/panyuqing/expose_experts/add_data/mpi/'+S_num+'/images'
    elif dset=='3dpw':
        S_num='train'
        add_path = '/data/panyuqing/expose_experts/add_data/3dpw/'+S_num+'/images'
    
    img_name=
    #'000000498079' #'000000123260' 

    if dset=='coco2017':
        save_3dparam_vertices_path=os.path.join(save_param_path, 'coco2017_raw',img_name + '.npy')
    elif dset=='mpi': 
        save_3dparam_vertices_path=os.path.join(save_param_path, 'mpi',S_num,img_name + '.npy')
    elif dset=='3dpw':
        save_3dparam_vertices_path=os.path.join(save_param_path, '3dpw_train',img_name + '.npy')
    elif dset=='curated_fits':
        save_3dparam_vertices_path=os.path.join(save_param_path, 'train.npy')

    # read 3d params and save vertices 
    if dset !='curated_fits':   
        param3d_vertices_data = np.load(save_3dparam_vertices_path, allow_pickle=True).item()
    
        global_pose_mat=batch_rodrigues(torch.from_numpy(param3d_vertices_data['global_orient']).reshape(1, 3)).reshape(-1, 1, 3, 3)
        body_pose_mat=batch_rodrigues(torch.from_numpy(param3d_vertices_data['body_pose'].reshape(21,3))).reshape(-1, 21, 3, 3)
        betas_for_vert_model=torch.from_numpy(param3d_vertices_data['betas']).reshape(1, -1)

        global_pose_mat=global_pose_mat.to(device)
        body_pose_mat=body_pose_mat.to(device)
        betas_for_vert_model=betas_for_vert_model.to(device)
        final_body_parameters = {
            'global_orient': global_pose_mat, 
            'body_pose': body_pose_mat,
            #'betas': betas_for_vert_model,
        }
        
        if 'jaw_pose' in param3d_vertices_data:
            expression_np=torch.from_numpy(param3d_vertices_data['expression']).reshape(1,-1)
            expression_np=expression_np.to(device)
            final_body_parameters['expression']= expression_np
            
            jaw_pose=torch.from_numpy(param3d_vertices_data['jaw_pose'])
            jaw_pose_mat=batch_rodrigues(jaw_pose).reshape(-1, 1, 3, 3)
            jaw_pose_mat=jaw_pose_mat.to(device)
            final_body_parameters['jaw_pose']= jaw_pose_mat.to(torch.float32)
            logger.info('has faces params!')
        
        if 'left_hand_pose' in param3d_vertices_data:      
            left_hand_pose=param3d_vertices_data['left_hand_pose']
            left_hand_pose_mat = batch_rodrigues(torch.from_numpy(left_hand_pose.reshape(15,3))).reshape(-1, 15, 3, 3)
            left_hand_pose_mat=left_hand_pose_mat.to(device)
            final_body_parameters['left_hand_pose']=left_hand_pose_mat.to(torch.float32) 
            #logger.info('left_hand_pose_mat: {}',left_hand_pose_mat)         

        if 'right_hand_pose' in param3d_vertices_data:      
            right_hand_pose=param3d_vertices_data['right_hand_pose']
            right_hand_pose_mat = batch_rodrigues(torch.from_numpy(right_hand_pose.reshape(15,3))).reshape(-1, 15, 3, 3)
            right_hand_pose_mat=right_hand_pose_mat.to(device)
            final_body_parameters['right_hand_pose']=right_hand_pose_mat.to(torch.float32)    
            #logger.info('right_hand_pose_mat: {}',right_hand_pose_mat)  
    
    else:
        bdata_all=np.load(expose_param_path, allow_pickle=True)
        img_fns = np.asarray(bdata_all['img_fns'], dtype=np.string_)
        img_fns_d=[img_fns[i].decode('utf-8') for i in range(len(img_fns))]#range(3000)]
        comp_img_fns=[osp.join('/data/panyuqing/expose_experts/data',ifn) for ifn in img_fns_d]

        for i in range(len(comp_img_fns)):
            img_fn=comp_img_fns[i]
            if img_fn.split('/')[-1].split('\\')[-1].split('.')[0]==img_name:
                print('index: ',i)
                print('img_fn: ',img_fn)
                pose=bdata_all['pose'][i]
                eye_offset = 0 if pose.shape[0] == 53 else 2
                
                body_pose=pose[1:22].reshape(1,63).astype(np.float32)
                global_orient=pose[0].reshape(1,3).astype(np.float32)
                left_hand_pose=pose[23 + eye_offset:23 + eye_offset + 15].reshape(1,45).astype(np.float32)
                right_hand_pose = pose[23 + 15 + eye_offset:].reshape(1,45).astype(np.float32)
                
                global_pose_mat=batch_rodrigues(torch.from_numpy(global_orient).reshape(1, 3)).reshape(-1, 1, 3, 3)
                body_pose_mat=batch_rodrigues(torch.from_numpy(body_pose.reshape(21,3))).reshape(-1, 21, 3, 3)
                global_pose_mat=global_pose_mat.to(device)
                body_pose_mat=body_pose_mat.to(device)
                left_hand_pose_mat = batch_rodrigues(torch.from_numpy(left_hand_pose.reshape(15,3))).reshape(-1, 15, 3, 3)
                left_hand_pose_mat=left_hand_pose_mat.to(device)
                right_hand_pose_mat = batch_rodrigues(torch.from_numpy(right_hand_pose.reshape(15,3))).reshape(-1, 15, 3, 3)
                right_hand_pose_mat=right_hand_pose_mat.to(device)

                final_body_parameters = {
                    'global_orient': global_pose_mat, 
                    'body_pose': body_pose_mat,
                    'left_hand_pose':left_hand_pose_mat,
                    'right_hand_pose':right_hand_pose_mat,
                }
                break

    model_path='/data/panyuqing/expose_experts/data/models'
    model_type='smplx'
    if use_frank_smplx:
        body_model = smplx.create(
            model_path, 
            model_type = model_type, 
            batch_size = 1,
            gender = 'neutral',
            num_betas = 10,
            use_pca = False,
            ext='pkl')#.cuda()
            
        right_hand_pose = torch.from_numpy(right_hand_pose).reshape(1,45).to(torch.float32) #pose_params[:, 3:]
        left_hand_pose = torch.from_numpy(left_hand_pose).reshape(1,45).to(torch.float32)
        #body_pose = torch.zeros((1, 63), dtype=torch.float32)
        #global_orient = torch.zeros((1, 3), dtype=torch.float32)
        body_pose = torch.from_numpy(param3d_vertices_data['body_pose']).reshape(1,63).to(torch.float32)
        global_orient = torch.from_numpy(param3d_vertices_data['global_orient']).reshape(1,3).to(torch.float32)
        #hand_rotation = pose_params[:, :3]
        #body_pose[:, 60:] = hand_rotation # set right hand rotation
        logger.info('left_hand_pose: {}',left_hand_pose.reshape(15,3))
        logger.info('right_hand_pose: {}',right_hand_pose.reshape(15,3))

        output = body_model(
            global_orient = global_orient,
            body_pose = body_pose,
            left_hand_pose = left_hand_pose,
            right_hand_pose = right_hand_pose,
            jaw_pose=jaw_pose.reshape(1,3).to(torch.float32),
            #betas = betas_for_vert_model,
            return_verts = True)
        whole_vertices = output['vertices'].detach().cpu().numpy()
        whole_vertices=whole_vertices.reshape((whole_vertices.shape[1],whole_vertices.shape[2]))

        open3dmesh=Mesh()
        open3dmesh.vertices = Vector3d(whole_vertices)
        open3dmesh.triangles = Vector3i(body_model.faces)#faces)#.detach().cpu().numpy())
    else: #use_expose_smplx_bodymodel
        # build smplify-x model to get pseudo gt vertices
        _C = CN()

        _C.body_model = CN()

        #_C.body_model.j14_regressor_path = '/data/panyuqing/expose_experts/data/SMPLX_to_J14.pkl'
        #_C.body_model.mean_pose_path = '/data/panyuqing/expose_experts/data/all_means.pkl'
        #_C.body_model.shape_mean_path = '/data/panyuqing/expose_experts/data/shape_mean.npy'
        _C.body_model.type = 'smplx'
        _C.body_model.model_folder = '/data/panyuqing/expose_experts/data/models'
        #_C.body_model.use_compressed = True
        _C.body_model.gender = 'neutral'
        _C.body_model.num_betas = 10
        _C.body_model.num_expression_coeffs = 10
        _C.body_model.use_pca = False
        _C.body_model.batch_size=1
        _C.body_model.ext='pkl'
        # _C.body_model.use_feet_keypoints = True
        # _C.body_model.use_face_keypoints = True
        # _C.body_model.use_face_contour = True

        # _C.body_model.global_orient = CN()
        # # The configuration for the parameterization of the body pose
        # _C.body_model.global_orient.param_type = 'cont_rot_repr'

        # _C.body_model.body_pose = CN()
        # # The configuration for the parameterization of the body pose
        # _C.body_model.body_pose.param_type = 'cont_rot_repr'
        #_C.body_model.body_pose.finetune = False

        #_C.body_model.left_hand_pose = CN()
        # The configuration for the parameterization of the left hand pose
        # _C.body_model.left_hand_pose.param_type = 'pca'
        # _C.body_model.left_hand_pose.num_pca_comps = 12
        # _C.body_model.left_hand_pose.flat_hand_mean = False
        # The type of prior on the left hand pose
        #_C.body_model.right_hand_pose = CN()
        # The configuration for the parameterization of the left hand pose
        #_C.body_model.right_hand_pose.param_type = 'pca'
        #_C.body_model.right_hand_pose.num_pca_comps = 12
        #_C.body_model.right_hand_pose.flat_hand_mean = False

        # _C.body_model.jaw_pose = CN()
        # _C.body_model.jaw_pose.param_type = 'cont_rot_repr'
        #_C.body_model.jaw_pose.data_fn = 'clusters.pkl'

        body_model_cfg=_C.get('body_model', {})
        body_model = build_body_model(
            model_path,
            model_type=model_type,
            dtype=torch.float32,
            **body_model_cfg)
        
        final_body_model_output = body_model(
            get_skin=True, return_shaped=True, **final_body_parameters)
        
        vertices=final_body_model_output['vertices'].detach().cpu().numpy()
        
        vertices=vertices.reshape((vertices.shape[1],vertices.shape[2]))
        
        #vertices=param3d_vertices_data['vertices']
        #logger.info('faces shape: {}',body_model.faces.shape)#(20908, 3)

        open3dmesh=Mesh()
        open3dmesh.vertices = Vector3d(vertices)
        open3dmesh.triangles = Vector3i(body_model.faces)#.detach().cpu().numpy())
    
    o3d.io.write_triangle_mesh('/data/panyuqing/expose_experts/expose/data/'+dset+'_'+img_name+'_regen.ply', open3dmesh)


        
