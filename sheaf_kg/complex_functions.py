import numpy as np
import pandas as pd
import pykeen
from pykeen.evaluation import  rank_based_evaluator
from tqdm import tqdm
import torch
import cvxpy as cp
from cvxpylayers.torch import CvxpyLayer
from scipy import stats

import sheaf_kg.batch_harmonic_extension as harmonic_extension

def L_p1_multisection(model, entities, relations, targets=None, invs=None):
    '''query of form ('e', ('r', 'r', ... , 'r')).
    here we assume 2 or more relations are present so 2p or greater
    '''
    proj_h, proj_t = model.project_t(torch.stack([entities, relations.flatten()], dim=1))
    scores = -torch.norm(proj_h[:, None, :, :] - proj_t[:, :, :, :], dim=(-2), p=model.scoring_fct_norm)**2
    return scores

def L_p_multisection(model, entities, relations, targets, invs=None):
    '''query of form ('e', ('r', 'r', ... , 'r')).
    here we assume 2 or more relations are present so 2p or greater
    '''
    all_ents = entities
    all_rels = relations
    all_invs = invs
    n_path_ents = all_rels.shape[1]
    num_queries = all_ents.shape[0]

    edge_indices = np.concatenate([np.arange(0,n_path_ents)[:,np.newaxis].T, np.arange(1,n_path_ents+1)[:,np.newaxis].T], axis=0)
    edge_indices = torch.LongTensor(np.repeat(edge_indices[np.newaxis, :, :], num_queries, axis=0))

    if len(model.left_embeddings) < 3:
        left_restrictions = torch.diag_embed(torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
        right_restrictions = torch.diag_embed(torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
    else:
        left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
        right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)

    restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
    if all_invs is not None:
        for ainvix in range(all_invs.shape[0]):
            invs = all_invs[ainvix]
            for invix in range(invs.shape[0]):
                if invs[invix] == -1:
                    tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
                    restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
                    restrictions[ainvix,invix,1,:,:] = tmp

    source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents)

    B = torch.LongTensor(np.repeat(np.array([0,n_path_ents],np.int)[np.newaxis,:], num_queries, axis=0))
    U = torch.LongTensor(np.repeat(np.array(range(1,n_path_ents),np.int)[np.newaxis,:], num_queries, axis=0))
    source_vertices = torch.LongTensor(np.zeros((num_queries,1), dtype=np.int)).to(model.device)
    target_vertices = torch.LongTensor(np.full((num_queries,1), 1, dtype=np.int)).to(model.device)
    LSchur = harmonic_extension.Kron_reduction(edge_indices, restrictions, B, U).to(model.device)
    target_embeddings = torch.index_select(model.ent_embeddings, 0, targets)
    Q = -harmonic_extension.compute_costs(LSchur,source_vertices,target_vertices,source_embeddings,target_embeddings,model.embedding_dim)
    return Q

def L_i_multisection(model, entities, relations, targets, invs=None):
    '''query of form (('e', ('r',)), ('e', ('r',)), ... , ('e', ('r',)))'''
    all_ents = entities
    all_rels = relations
    all_invs = invs
    n_ents = all_ents.shape[1]
    num_intersects = all_ents.shape[1]
    num_queries = all_ents.shape[0]

    edge_indices = np.concatenate([np.full(n_ents,n_ents)[:,np.newaxis].T, np.arange(0,n_ents)[:,np.newaxis].T], axis=0)
    edge_indices = torch.LongTensor(np.repeat(edge_indices[np.newaxis, :, :], num_queries, axis=0))

    if len(model.left_embeddings) < 3:
        left_restrictions = torch.diag_embed(torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
        right_restrictions = torch.diag_embed(torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
    else:
        left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
        right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)

    restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
    if all_invs is not None:
        for ainvix in range(len(all_invs)):
            invs = all_invs[ainvix]
            for invix in range(len(invs)):
                if invs[invix] == -1:
                    tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
                    restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
                    restrictions[ainvix,invix,1,:,:] = tmp

    source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents.flatten()).view(all_ents.shape[0], -1, model.num_sections)

    L = harmonic_extension.Laplacian(edge_indices, restrictions).to(model.device)
    source_vertices = torch.LongTensor(np.repeat(np.arange(n_ents)[np.newaxis,:], num_queries, axis=0)).to(model.device)
    target_vertices = torch.LongTensor(np.full((num_queries, 1),n_ents, dtype=np.int)).to(model.device)
    target_embeddings = torch.index_select(model.ent_embeddings, 0, targets).view(-1, model.embedding_dim, model.num_sections)
    Q = -harmonic_extension.compute_costs(L,source_vertices,target_vertices,source_embeddings,target_embeddings,model.embedding_dim)
    return Q

def L_ip_multisection(model, entities, relations, targets, invs=None):
    '''query of form ((('e', ('r',)), ('e', ('r',))), ('r',))'''
    all_ents = entities
    all_rels = relations
    all_invs = invs
    n_ents = all_ents.shape[1]
    num_queries = all_ents.shape[0]

    edge_indices = torch.LongTensor(np.repeat(np.array([[0,2],[1,2],[2,3]],np.int).T[np.newaxis,:,:], num_queries, axis=0))

    if len(model.left_embeddings) < 3:
        left_restrictions = torch.diag_embed(torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
        right_restrictions = torch.diag_embed(torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
    else:
        left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
        right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)

    restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
    if all_invs is not None:
        for ainvix in range(all_invs.shape[0]):
            invs = all_invs[ainvix]
            for invix in range(invs.shape[0]):
                if invs[invix] == -1:
                    tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
                    restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
                    restrictions[ainvix,invix,1,:,:] = tmp

    source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents.flatten()).view(all_ents.shape[0], -1, model.num_sections)

    B = torch.LongTensor(np.repeat(np.array([0,2,3],dtype=np.int)[np.newaxis,:], num_queries, axis=0))
    U = torch.LongTensor(np.full((num_queries,1), 1, dtype=np.int))
    source_vertices = torch.LongTensor(np.repeat(np.array([0,1], dtype=np.int)[np.newaxis,:], num_queries, axis=0)).to(model.device)
    target_vertices = torch.LongTensor(np.full((num_queries,1), 2, dtype=np.int)).to(model.device)
    LSchur = harmonic_extension.Kron_reduction(edge_indices, restrictions, B, U).to(model.device)
    target_embeddings = torch.index_select(model.ent_embeddings, 0, targets).view(-1, model.embedding_dim, model.num_sections)
    Q = -harmonic_extension.compute_costs(LSchur,source_vertices,target_vertices,source_embeddings,target_embeddings,model.embedding_dim)
    return Q

def L_pi_multisection(model, entities, relations, targets, invs=None):
    '''query of form (('e', ('r', 'r')), ('e', ('r',)))'''
    all_ents = entities
    all_rels = relations
    all_invs = invs
    n_ents = all_ents.shape[1]
    num_queries = all_ents.shape[0]

    edge_indices = torch.LongTensor(np.repeat(np.array([[0,2],[2,3],[1,3]],np.int).T[np.newaxis,:,:], num_queries, axis=0))

    if len(model.left_embeddings) < 3:
        left_restrictions = torch.diag_embed(torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
        right_restrictions = torch.diag_embed(torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
    else:
        left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
        right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)

    restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
    if all_invs is not None:
        for ainvix in range(all_invs.shape[0]):
            invs = all_invs[ainvix]
            for invix in range(invs.shape[0]):
                if invs[invix] == -1:
                    tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
                    restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
                    restrictions[ainvix,invix,1,:,:] = tmp

    source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents.flatten()).view(all_ents.shape[0], -1, model.num_sections)

    B = torch.LongTensor(np.repeat(np.array([0,1,3], dtype=np.int)[np.newaxis, :], num_queries, axis=0))
    U = torch.LongTensor(np.full((num_queries, 1), 2, dtype=np.int))
    source_vertices = torch.LongTensor(np.repeat(np.array([0,1], dtype=np.int).T[np.newaxis,:], num_queries, axis=0)).to(model.device)
    target_vertices = torch.LongTensor(np.full((num_queries,1), 2, dtype=np.int)).to(model.device)
    LSchur = harmonic_extension.Kron_reduction(edge_indices, restrictions, B, U).to(model.device)
    target_embeddings = torch.index_select(model.ent_embeddings, 0, targets).view(-1, model.embedding_dim, model.num_sections)
    Q = -harmonic_extension.compute_costs(LSchur,source_vertices,target_vertices,source_embeddings,target_embeddings,model.embedding_dim)
    return Q

def L_p1_translational(model, entities, relations, targets=None, invs=None):
    '''query of form ('e', ('r', 'r', ... , 'r')).
    here we assume 2 or more relations are present so 2p or greater
    '''
    proj_h, proj_t = model.project_t(torch.stack([entities, relations.flatten()], dim=1))
    c = torch.index_select(model.edge_cochains, 0, relations.flatten()).view(-1, model.edge_stalk_dim, model.num_sections)
    scores = -torch.norm(proj_h[:, None, :, :] + c[:, None, :, :] - proj_t[:, :, :, :], dim=(-2), p=model.scoring_fct_norm)**2
    return scores

def L_p_translational(model, entities, relations, targets, invs=None):
    '''query of form ('e', ('r', 'r', ... , 'r')).
    here we assume 2 or more relations are present so 2p or greater
    '''
    all_ents = entities
    all_rels = relations
    all_invs = invs
    n_path_ents = all_rels.shape[1]
    num_queries = all_ents.shape[0]

    edge_indices = np.concatenate([np.arange(0,n_path_ents)[:,np.newaxis].T, np.arange(1,n_path_ents+1)[:,np.newaxis].T], axis=0)
    edge_indices = torch.LongTensor(np.repeat(edge_indices[np.newaxis, :, :], num_queries, axis=0))

    if len(model.left_embeddings) < 3:
        left_restrictions = torch.diag_embed(torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
        right_restrictions = torch.diag_embed(torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
    else:
        left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
        right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)


    restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
    if all_invs is not None:
        for ainvix in range(all_invs.shape[0]):
            invs = all_invs[ainvix]
            for invix in range(invs.shape[0]):
                if invs[invix] == -1:
                    tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
                    restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
                    restrictions[ainvix,invix,1,:,:] = tmp

    source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents)
    # flatten into long vector of 1-cochains
    b = torch.index_select(model.edge_cochains, 0, all_rels.flatten()).view(all_rels.shape[0], -1, model.num_sections)

    B = torch.LongTensor(np.repeat(np.array([0,n_path_ents],np.int)[np.newaxis,:], num_queries, axis=0))
    U = torch.LongTensor(np.repeat(np.array(range(1,n_path_ents),np.int)[np.newaxis,:], num_queries, axis=0))
    source_vertices = torch.LongTensor(np.zeros((num_queries,1), dtype=np.int)).to(model.device)
    target_vertices = torch.LongTensor(np.full((num_queries,1), 1, dtype=np.int)).to(model.device)
    LSchur, affine = harmonic_extension.Kron_reduction_translational(edge_indices, restrictions, B, U)
    LSchur = LSchur.to(model.device)
    affine = affine.to(model.device)
    target_embeddings = torch.index_select(model.ent_embeddings, 0, targets)
    Q = -harmonic_extension.compute_costs_translational(LSchur,affine,source_vertices,target_vertices,source_embeddings,target_embeddings,b,model.embedding_dim)
    return Q

def L_i_translational(model, entities, relations, targets, invs=None):
    '''query of form (('e', ('r',)), ('e', ('r',)), ... , ('e', ('r',)))'''
    all_ents = entities
    all_rels = relations
    all_invs = invs
    n_ents = all_ents.shape[1]
    num_intersects = all_ents.shape[1]
    num_queries = all_ents.shape[0]

    edge_indices = np.concatenate([np.full(n_ents,n_ents)[:,np.newaxis].T, np.arange(0,n_ents)[:,np.newaxis].T], axis=0)
    edge_indices = torch.LongTensor(np.repeat(edge_indices[np.newaxis, :, :], num_queries, axis=0))

    if len(model.left_embeddings) < 3:
        left_restrictions = torch.diag_embed(torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
        right_restrictions = torch.diag_embed(torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
    else:
        left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
        right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)

    restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
    if all_invs is not None:
        for ainvix in range(len(all_invs)):
            invs = all_invs[ainvix]
            for invix in range(len(invs)):
                if invs[invix] == 1:
                    tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
                    restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
                    restrictions[ainvix,invix,1,:,:] = tmp

    source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents.flatten()).view(all_ents.shape[0], -1, model.num_sections)
    # flatten into long vector of 1-cochains
    b = torch.index_select(model.edge_cochains, 0, all_rels.flatten()).view(all_rels.shape[0],-1,model.num_sections)

    L = harmonic_extension.Laplacian(edge_indices, restrictions).to(model.device)
    d = harmonic_extension.coboundary(edge_indices, restrictions).to(model.device)
    source_vertices = torch.LongTensor(np.repeat(np.arange(n_ents)[np.newaxis,:], num_queries, axis=0)).to(model.device)
    target_vertices = torch.LongTensor(np.full((num_queries, 1),n_ents, dtype=np.int)).to(model.device)
    target_embeddings = torch.index_select(model.ent_embeddings, 0, targets).view(-1, model.embedding_dim, model.num_sections)
    Q = -harmonic_extension.compute_costs_translational(L,d,source_vertices,target_vertices,source_embeddings,target_embeddings,b,model.embedding_dim)
    return Q

def L_ip_translational(model, entities, relations, targets, invs=None):
    '''query of form ((('e', ('r',)), ('e', ('r',))), ('r',))'''
    all_ents = entities
    all_rels = relations
    all_invs = invs
    n_ents = all_ents.shape[1]
    num_queries = all_ents.shape[0]

    edge_indices = torch.LongTensor(np.repeat(np.array([[0,2],[1,2],[2,3]],np.int).T[np.newaxis,:,:], num_queries, axis=0))

    if len(model.left_embeddings) < 3:
        left_restrictions = torch.diag_embed(torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
        right_restrictions = torch.diag_embed(torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
    else:
        left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
        right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)

    restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
    if all_invs is not None:
        for ainvix in range(all_invs.shape[0]):
            invs = all_invs[ainvix]
            for invix in range(invs.shape[0]):
                if invs[invix] == -1:
                    tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
                    restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
                    restrictions[ainvix,invix,1,:,:] = tmp

    source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents.flatten()).view(all_ents.shape[0], -1, model.num_sections)
    # flatten into long vector of 1-cochains
    b = torch.index_select(model.edge_cochains, 0, all_rels.flatten()).view(all_rels.shape[0],-1,model.num_sections)

    B = torch.LongTensor(np.repeat(np.array([0,2,3],dtype=np.int)[np.newaxis,:], num_queries, axis=0))
    U = torch.LongTensor(np.full((num_queries,1), 1, dtype=np.int))
    source_vertices = torch.LongTensor(np.repeat(np.array([0,1], dtype=np.int)[np.newaxis,:], num_queries, axis=0)).to(model.device)
    target_vertices = torch.LongTensor(np.full((num_queries,1), 2, dtype=np.int)).to(model.device)
    LSchur, affine = harmonic_extension.Kron_reduction_translational(edge_indices, restrictions, B, U)
    LSchur = LSchur.to(model.device)
    affine = affine.to(model.device)
    target_embeddings = torch.index_select(model.ent_embeddings, 0, targets).view(-1, model.embedding_dim, model.num_sections)
    Q = -harmonic_extension.compute_costs_translational(LSchur,affine,source_vertices,target_vertices,source_embeddings,target_embeddings,b,model.embedding_dim)
    return Q

def L_pi_translational(model, entities, relations, targets, invs=None):
    '''query of form (('e', ('r', 'r')), ('e', ('r',)))'''
    all_ents = entities
    all_rels = relations
    all_invs = invs
    n_ents = all_ents.shape[1]
    num_queries = all_ents.shape[0]

    edge_indices = torch.LongTensor(np.repeat(np.array([[0,2],[2,3],[1,3]],np.int).T[np.newaxis,:,:], num_queries, axis=0))

    if len(model.left_embeddings) < 3:
        left_restrictions = torch.diag_embed(torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
        right_restrictions = torch.diag_embed(torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.embedding_dim))
    else:
        left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
        right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)

    restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
    if all_invs is not None:
        for ainvix in range(all_invs.shape[0]):
            invs = all_invs[ainvix]
            for invix in range(invs.shape[0]):
                if invs[invix] == -1:
                    tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
                    restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
                    restrictions[ainvix,invix,1,:,:] = tmp

    source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents.flatten()).view(all_ents.shape[0], -1, model.num_sections)
    # flatten into long vector of 1-cochains
    b = torch.index_select(model.edge_cochains, 0, all_rels.flatten()).view(all_rels.shape[0],-1,model.num_sections)

    B = torch.LongTensor(np.repeat(np.array([0,1,3], dtype=np.int)[np.newaxis, :], num_queries, axis=0))
    U = torch.LongTensor(np.full((num_queries, 1), 2, dtype=np.int))
    source_vertices = torch.LongTensor(np.repeat(np.array([0,1], dtype=np.int).T[np.newaxis,:], num_queries, axis=0)).to(model.device)
    target_vertices = torch.LongTensor(np.full((num_queries,1), 2, dtype=np.int)).to(model.device)
    LSchur, affine = harmonic_extension.Kron_reduction_translational(edge_indices, restrictions, B, U)
    LSchur = LSchur.to(model.device)
    affine = affine.to(model.device)
    target_embeddings = torch.index_select(model.ent_embeddings, 0, targets).view(-1, model.embedding_dim, model.num_sections)
    Q = -harmonic_extension.compute_costs_translational(LSchur,affine,source_vertices,target_vertices,source_embeddings,target_embeddings,b,model.embedding_dim)
    return Q

def cvxpy_problem(edge_index,dv,de,input_nodes):
    nv = torch.max(torch.max(edge_index)).item() + 1
    ne = edge_index.shape[1]
    x = cp.Variable(nv*dv)
    d = cp.Parameter((ne*de,nv*dv))
    xB = cp.Parameter(len(input_nodes)*dv)
    norm_constraints = [cp.norm(x[i*dv:(i+1)*dv]) <= 1 for i in range(nv) if i not in input_nodes]
    boundary_constraints = [x[v*dv:(v+1)*dv] == xB[i*dv:(i+1)*dv] for (i,v) in enumerate(input_nodes)]
    constraints = norm_constraints + boundary_constraints
    objective = cp.Minimize(cp.norm(d @ x))
    problem = cp.Problem(objective,constraints=constraints)

    layer = CvxpyLayer(problem, parameters=[d,xB], variables=[x])

    return layer

def linear_chain(ne):
    edge_index = torch.zeros((2,ne),dtype=torch.int)
    for e in range(ne):
        edge_index[0,e] = e
        edge_index[1,e] = e + 1
    return edge_index

def ip_chain():
    return torch.LongTensor(np.array([[0,2],[1,2],[2,3]],np.int).T[np.newaxis,:,:])

def pi_chain():
    return torch.LongTensor(np.array([[0,2],[2,3],[1,3]],np.int).T[np.newaxis,:,:])

def L_p_cvx(model, entities, relations, targets, invs=None, sec=0, layer=None):
    if layer is None:
        raise ValueError('Must specify cvxlayer instantiation')
    all_ents = entities
    all_rels = relations
    all_invs = invs
    targets = targets
    n_path_ents = all_rels.shape[1]
    num_queries = all_ents.shape[0]

    edge_indices = np.concatenate([np.arange(0,n_path_ents)[:,np.newaxis].T, np.arange(1,n_path_ents+1)[:,np.newaxis].T], axis=0)
    edge_indices = torch.LongTensor(np.repeat(edge_indices[np.newaxis, :, :], num_queries, axis=0))

    left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
    right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)

    restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
    if all_invs is not None:
        for ainvix in range(all_invs.shape[0]):
            invs = all_invs[ainvix]
            for invix in range(invs.shape[0]):
                if invs[invix] == -1:
                    tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
                    restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
                    restrictions[ainvix,invix,1,:,:] = tmp

    source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents)[:,:,sec]
    target_embeddings = torch.index_select(model.ent_embeddings, 0, targets)[:,:,sec].to('cpu')

    d = harmonic_extension.coboundary(edge_indices, restrictions)
    ret = torch.empty((num_queries, targets.shape[0]))
    for qix in range(num_queries):
        xopts, = layer(d[qix].to('cpu'), source_embeddings[qix].flatten().to('cpu'))
        r = xopts.reshape((-1,source_embeddings.shape[1]))
        t = r[-1]
        ret[qix,:] = -torch.linalg.norm(t[None,:] - target_embeddings, ord=2, dim=1)
#         ret[qix,:] = -torch.matmul(target_embeddings, t)
    return ret

# def L_p2_cvx(model, entities, relations, targets, invs=None, sec=0):
#     all_ents = entities
#     all_rels = relations
#     all_invs = invs
#     targets = targets
#     n_path_ents = all_rels.shape[1]
#     num_queries = all_ents.shape[0]
#
#     edge_indices = np.concatenate([np.arange(0,n_path_ents)[:,np.newaxis].T, np.arange(1,n_path_ents+1)[:,np.newaxis].T], axis=0)
#     edge_indices = torch.LongTensor(np.repeat(edge_indices[np.newaxis, :, :], num_queries, axis=0))
#
#     left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
#     right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
#
#     restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
#     if all_invs is not None:
#         for ainvix in range(all_invs.shape[0]):
#             invs = all_invs[ainvix]
#             for invix in range(invs.shape[0]):
#                 if invs[invix] == -1:
#                     tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
#                     restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
#                     restrictions[ainvix,invix,1,:,:] = tmp
#
#     source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents)[:,:,sec]
#     target_embeddings = torch.index_select(model.ent_embeddings, 0, targets)[:,:,sec].to('cpu')
#
#     d = harmonic_extension.coboundary(edge_indices, restrictions)
#     ret = torch.empty((num_queries, targets.shape[0]))
#     for qix in range(num_queries):
#         xopts, = layer(d[qix].to('cpu'), source_embeddings[qix].flatten().to('cpu'))
#         r = xopts.reshape((-1,source_embeddings.shape[1]))
#         t = r[-1]
#         ret[qix,:] = -torch.linalg.norm(t[None,:] - target_embeddings, ord=2, dim=1)
# #         ret[qix,:] = -torch.matmul(target_embeddings, t)
#     return ret
#
# def L_p3_cvx(model, entities, relations, targets, invs=None, sec=0):
#     all_ents = entities
#     all_rels = relations
#     all_invs = invs
#     targets = targets
#     n_path_ents = all_rels.shape[1]
#     num_queries = all_ents.shape[0]
#
#     edge_indices = np.concatenate([np.arange(0,n_path_ents)[:,np.newaxis].T, np.arange(1,n_path_ents+1)[:,np.newaxis].T], axis=0)
#     edge_indices = torch.LongTensor(np.repeat(edge_indices[np.newaxis, :, :], num_queries, axis=0))
#
#     left_restrictions = torch.index_select(model.left_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
#     right_restrictions = torch.index_select(model.right_embeddings, 0, all_rels.flatten()).view(-1, all_rels.shape[1], model.edge_stalk_dim, model.embedding_dim)
#
#     restrictions = torch.cat((left_restrictions.unsqueeze(2), right_restrictions.unsqueeze(2)), dim=2)
#     if all_invs is not None:
#         for ainvix in range(all_invs.shape[0]):
#             invs = all_invs[ainvix]
#             for invix in range(invs.shape[0]):
#                 if invs[invix] == -1:
#                     tmp = torch.clone(restrictions[ainvix,invix,0,:,:])
#                     restrictions[ainvix,invix,0,:,:] = restrictions[ainvix,invix,1,:,:]
#                     restrictions[ainvix,invix,1,:,:] = tmp
#
#     source_embeddings = torch.index_select(model.ent_embeddings, 0, all_ents)[:,:,sec]
#     target_embeddings = torch.index_select(model.ent_embeddings, 0, targets)[:,:,sec].to('cpu')
#
#     d = harmonic_extension.coboundary(edge_indices, restrictions)
#     ret = torch.empty((num_queries, targets.shape[0]))
#     for qix in range(num_queries):
#         xopts, = layer_3p(d[qix].to('cpu'), source_embeddings[qix].flatten().to('cpu'))
#         r = xopts.reshape((-1,source_embeddings.shape[1]))
#         t = r[-1]
# #         ret[qix,:] = -torch.matmul(target_embeddings, t)
#         ret[qix,:] = -torch.linalg.norm(t[None,:] - target_embeddings, ord=2, dim=1)
#     return ret


def L_ip_cvx(model, entities, relations, targets, invs=None, sec=0):
    raise ValueError('not implemented')

def L_pi_cvx(model, entities, relations, targets, invs=None, sec=0):
    raise ValueError('not implemented')

def test_batch(model, test_data, model_inverses=False, sec=0, test_batch_size=5,
                test_query_structures=['1p','2p','3p','2i','3i','ip','pi'],
                ks=[1,3,5,10], complex_solver='schur'):
    with torch.no_grad():
        results = []
        for query_structure in test_query_structures:
            print('Running query : {}'.format(query_structure))
            all_avg_ranks = []
            cnt = 0
            num_test = len(test_data[query_structure]['answers'])
            for qix in tqdm(range(0, num_test//2, test_batch_size)):
                if num_test - qix == 1:
                    continue
                entities = test_data[query_structure]['entities'][qix:qix+test_batch_size]
                relations = test_data[query_structure]['relations'][qix:qix+test_batch_size]
                if model_inverses:
                    inverses = None
                else:
                    inverses = test_data[query_structure]['inverses'][qix:qix+test_batch_size]
                all_answers = test_data[query_structure]['answers'][qix:qix+test_batch_size]
                targets = torch.arange(model.num_entities).to(model.device)
                Q = model.forward_costs(query_structure, entities, relations, targets, invs=inverses)
                if complex_solver == 'schur':
                    Q = Q[:,:,sec]
                max_len = len(max(all_answers, key=len))
                for i in range(max_len):
                    answers = [a[i] if len(a) > i else a[-1] for a in all_answers ]
                    if len(answers) > 0:
                        ranks = rank_based_evaluator.compute_rank_from_scores(Q[np.vstack((np.arange(len(answers)), answers))].unsqueeze(1), Q)
                        avg_rank = ranks['avg'].cpu().numpy()
                        all_avg_ranks.append(avg_rank)
            all_avg_ranks = np.concatenate(all_avg_ranks)
            rd = {k: np.mean(all_avg_ranks <= k) for k in ks}
            rd['mrr'] = np.reciprocal(stats.hmean(all_avg_ranks))
            # rd['mr'] = np.mean(all_avg_ranks)
            results.append(rd)

        df = pd.DataFrame(results, index=test_query_structures)
        return df