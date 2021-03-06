# python anim.py  --nbiter 1000000 --rule oja --squash 0 --hiddensize 200  --lr 1e-4 --eplen 250 --print_every 200 --save_every 1000  --bentropy 0.1 --blossv .03 --randstart 1 --gr .9 --rp 0 --labsize 11 --rngseed 1 --type plastic


import argparse
import pdb 
import torch
import torch.nn as nn
from torch.autograd import Variable
import numpy as np
from numpy import random
import torch.nn.functional as F
from torch import optim
from torch.optim import lr_scheduler
import random
import sys
import pickle
import time
import os
import OpusHdfsCopy
from OpusHdfsCopy import transferFileToHdfsDir, checkHdfs
import platform

import gridlab
from gridlab import Network

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import glob





np.set_printoptions(precision=4)

ETA = .02  # Not used

ADDINPUT = 4 # 1 input for the previous reward, 1 input for numstep, 1 for whether currently on reward square, 1 "Bias" input

NBACTIONS = 4  # U, D, L, R

RFSIZE = 3 # Receptive Field

TOTALNBINPUTS =  RFSIZE * RFSIZE + ADDINPUT + NBACTIONS


fig = plt.figure()
plt.axis('off')

def train(paramdict):
    #params = dict(click.get_current_context().params)
    print("Starting training...")
    params = {}
    #params.update(defaultParams)
    params.update(paramdict)
    print("Passed params: ", params)
    print(platform.uname())
    #params['nbsteps'] = params['nbshots'] * ((params['prestime'] + params['interpresdelay']) * params['nbclasses']) + params['prestimetest']  # Total number of steps per episode


    # This needs to be the same as in the file generated by gridlab, and thus the command line parameters must be identical
    suffix = "grid_"+"".join([str(x)+"_" if pair[0] is not 'nbsteps' and pair[0] is not 'rngseed' and pair[0] is not 'save_every' and pair[0] is not 'test_every' else '' for pair in sorted(zip(params.keys(), params.values()), key=lambda x:x[0] ) for x in pair])[:-1] + "_rngseed_" + str(params['rngseed'])   # Turning the parameters into a nice suffix for filenames


    params['rngseed'] = 3
    # Initialize random seeds (first two redundant?)
    print("Setting random seeds")
    np.random.seed(params['rngseed']); random.seed(params['rngseed']); torch.manual_seed(params['rngseed'])
    #print(click.get_current_context().params)
    
    net = Network(params)
    net.load_state_dict(torch.load('./tmpWorked/torchmodel_'+suffix + '.txt'))


    print ("Shape of all optimized parameters:", [x.size() for x in net.parameters()])
    allsizes = [torch.numel(x.data.cpu()) for x in net.parameters()]
    print ("Size (numel) of all optimized elements:", allsizes)
    print ("Total size (numel) of all optimized elements:", sum(allsizes))

    #total_loss = 0.0
    print("Initializing optimizer")
    optimizer = torch.optim.Adam(net.parameters(), lr=1.0*params['lr'], eps=1e-4)
    #optimizer = torch.optim.SGD(net.parameters(), lr=1.0*params['lr'])
    #scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, params['gamma']) 
    #scheduler = torch.optim.lr_scheduler.StepLR(optimizer, gamma=params['gamma'], step_size=params['steplr'])

    LABSIZE = params['labsize'] 
    lab = np.ones((LABSIZE, LABSIZE))
    CTR = LABSIZE // 2 

    # Simple cross maze
    #lab[CTR, 1:LABSIZE-1] = 0
    #lab[1:LABSIZE-1, CTR] = 0


    # Double-T maze
    #lab[CTR, 1:LABSIZE-1] = 0
    #lab[1:LABSIZE-1, 1] = 0
    #lab[1:LABSIZE-1, LABSIZE - 2] = 0

    # Grid maze
    lab[1:LABSIZE-1, 1:LABSIZE-1].fill(0)
    for row in range(1, LABSIZE - 1):
        for col in range(1, LABSIZE - 1):
            if row % 2 == 0 and col % 2 == 0:
                lab[row, col] = 1
    lab[CTR,CTR] = 0 # Not strictly necessary, but perhaps helps loclization by introducing a detectable irregularity in the center



    all_losses = []
    all_losses_objective = []
    all_losses_eval = []
    all_losses_v = []
    lossbetweensaves = 0
    nowtime = time.time()
    
    print("Starting episodes...")
    sys.stdout.flush()

    pos = 0
    hidden = net.initialZeroState()
    hebb = net.initialZeroHebb()


    # Starting episodes!
    
    params['nbiter'] = 1
    
    for numiter in range(params['nbiter']):
        
        PRINTTRACE = 0
        if (numiter+1) % (1 + params['print_every']) == 0:
            PRINTTRACE = 1

        ## Where is the reward square for this episode?
        
        #rnd = np.random.randint(0,4) 
        ##if rnd == 0:
        ##    rposr = 1; rposc = CTR
        ##elif rnd == 1:
        ##    rposr = CTR; rposc = 1
        ##elif rnd == 2:
        ##    rposr = CTR; rposc = LABSIZE - 2
        ##elif rnd == 3:
        ##    rposr = LABSIZE - 2; rposc = CTR
        #if rnd == 0:
        #    rposr = 1; rposc = 1 
        #elif rnd == 1:
        #    rposr = LABSIZE - 2; rposc = 1
        #elif rnd == 2:
        #    rposr = 1; rposc = LABSIZE - 2
        #elif rnd == 3:
        #    rposr = LABSIZE - 2; rposc = LABSIZE - 2

        # Note: it doesn't matter if the reward is on the center (see below). All we need is not to put it on a wall or pillar (lab=1)
        rposr = 0; rposc = 0
        if params['rp'] == 0:
            while lab[rposr, rposc] == 1:
                rposr = np.random.randint(1, LABSIZE - 1)
                rposc = np.random.randint(1, LABSIZE - 1)
        elif params['rp'] == 1:
            while lab[rposr, rposc] == 1 or (rposr != 1 and rposr != LABSIZE -2 and rposc != 1 and rposc != LABSIZE-2):
                rposr = np.random.randint(1, LABSIZE - 1)
                rposc = np.random.randint(1, LABSIZE - 1)
        #print("Reward pos:", rposr, rposc)

        # Agent always starts an episode from the center
        posc = CTR
        posr = CTR

        optimizer.zero_grad()
        loss = 0
        lossv = 0
        hidden = net.initialZeroState()
        hebb = net.initialZeroHebb()


        reward = 0.0
        rewards = []
        vs = []
        logprobs = []
        sumreward = 0.0
        dist = 0


        #params['print_every'] = 10



        print("==========")
        print("==========")

        ax_imgs = []

        for numstep in range(params['eplen']):
            
            
            inputsN = np.zeros((1, TOTALNBINPUTS), dtype='float32')
            inputsN[0, 0:RFSIZE * RFSIZE] = lab[posr - RFSIZE//2:posr + RFSIZE//2 +1, posc - RFSIZE //2:posc + RFSIZE//2 +1].flatten()
            
            inputs = torch.from_numpy(inputsN).cuda()
            # Previous chosen action
            #inputs[0][numactionchosen] = 1
            inputs[0][-1] = 1 # Bias neuron
            inputs[0][-2] = numstep
            inputs[0][-3] = reward
            #if rposr == posr and rposc = posc:
            #    inputs[0][-4] = 1
            #else:
            #    inputs[0][-4] = 0
            
            # Running the network
            y, v, hidden, hebb = net(Variable(inputs, requires_grad=False), hidden, hebb)  # y  should output probabilities
        
            distrib = torch.distributions.Categorical(y)
            actionchosen = distrib.sample()  # sample() returns a Pytorch tensor of size 1; this is needed for the backprop below
            numactionchosen = actionchosen.data[0]    # Turn to scalar

            tgtposc = posc
            tgtposr = posr
            if numactionchosen == 0:  # Up
                tgtposr -= 1
            elif numactionchosen == 1:  # Down
                tgtposr += 1
            elif numactionchosen == 2:  # Left
                tgtposc -= 1
            elif numactionchosen == 3:  # Right
                tgtposc += 1
            else:
                raise ValueError("Wrong Action")
            
            reward = 0.0
            if lab[tgtposr][tgtposc] == 1:
                reward = -.1
            else:
                dist += 1
                posc = tgtposc
                posr = tgtposr

            # Display the labyrinth

            for numr in range(LABSIZE):
                s = ""
                for numc in range(LABSIZE):
                    if posr == numr and posc == numc:
                        s += "o"
                    elif rposr == numr and rposc == numc:
                        s += "X"
                    elif lab[numr, numc] == 1:
                        s += "#"
                    else:
                        s += " "
                print(s)
            print("")
            print("")

            labg = lab.copy()
            labg[rposr, rposc] = 2
            labg[posr, posc] = 3
            fullimg = plt.imshow(labg, animated=True)
            ax_imgs.append([fullimg])  




            # Did we hit the reward location ? Increase reward and teleport!
            # Note that it doesn't matter if we teleport onto the reward, since reward hitting is only evaluated after the (obligatory) move
            if rposr == posr and rposc == posc:
                reward += 10
                if params['randstart'] == 1:
                    posr = np.random.randint(1, LABSIZE - 1)
                    posc = np.random.randint(1, LABSIZE - 1)
                    while lab[posr, posc] == 1:
                        posr = np.random.randint(1, LABSIZE - 1)
                        posc = np.random.randint(1, LABSIZE - 1)
                else:
                    posr = CTR
                    posc = CTR



            rewards.append(reward)
            vs.append(v)
            sumreward += reward
            
            #loss -= distrib.log_prob(actionchosen)  # * reward
            logprobs.append(distrib.log_prob(actionchosen))

            loss += params['bentropy'] * y.pow(2).sum()   # We want to penalize concentration, i.e. encourage diversity; our version of PyTorch does not have an entropy() function for Distribution. Note: .2 may be too strong, .04 may be too weak. 

            #if PRINTTRACE:
            #    print("Probabilities:", y.data.cpu().numpy(), "Picked action:", numactionchosen, ", got reward", reward)

        R = 0
        gammaR = params['gr']
        for numstepb in reversed(range(params['eplen'])) :
            R = gammaR * R + rewards[numstepb]
            lossv += (vs[numstepb][0] - R).pow(2) 
            loss -= logprobs[numstepb] * (R - vs[numstepb].data[0][0])  # Not sure if the "data" is needed... put it b/c of worry about weird gradient flows



        if True: #PRINTTRACE:
            print("lossv: ", lossv.data.cpu().numpy()[0])
            print ("Total reward for this episode:", sumreward, "Dist:", dist)

        if params['squash'] == 1:
            if sumreward < 0:
                sumreward = -np.sqrt(-sumreward)
            else:
                sumreward = np.sqrt(sumreward)
        elif params['squash'] == 0:
            pass
        else:
            raise ValueError("Incorrect value for squash parameter")

        #loss *= sumreward
        loss += params['blossv'] * lossv
        loss /= params['eplen']
        
        #loss.backward()

        ##for p in net.parameters():
        ##    p.grad.data.clamp_(-params['clamp'], params['clamp'])
        #scheduler.step()
        #optimizer.step()

        #torch.cuda.empty_cache()  

        lossnum = loss.data[0]
        lossbetweensaves += lossnum
        if (numiter + 1) % 10 == 0:
            all_losses_objective.append(lossnum)
            all_losses_eval.append(sumreward)
            all_losses_v.append(lossv.data[0])
        #total_loss  += lossnuma

        anim = animation.ArtistAnimation(fig, ax_imgs, interval=200)
        anim.save('anim.gif', writer='imagemagick', fps=10)


        if (numiter+1) % params['print_every'] == 0:

            print(numiter, "====")
            print("Mean loss: ", lossbetweensaves / params['print_every'])
            lossbetweensaves = 0
            previoustime = nowtime
            nowtime = time.time()
            print("Time spent on last", params['print_every'], "iters: ", nowtime - previoustime)
            if params['type'] == 'plastic' or params['type'] == 'lstmplastic':
                print("ETA: ", net.eta.data.cpu().numpy(), "alpha[0,1]: ", net.alpha.data.cpu().numpy()[0,1], "w[0,1]: ", net.w.data.cpu().numpy()[0,1] )
            elif params['type'] == 'rnn':
                print("w[0,1]: ", net.w.data.cpu().numpy()[0,1] )

        if (numiter+1) % params['save_every'] == 0:
            print("Saving files...")
#            lossbetweensaves /= params['save_every']
#            print("Average loss over the last", params['save_every'], "episodes:", lossbetweensaves)
#            print("Alternative computation (should be equal):", np.mean(all_losses_objective[-params['save_every']:]))
            losslast100 = np.mean(all_losses_objective[-100:])
            print("Average loss over the last 100 episodes:", losslast100)
#            # Instability detection; necessary for SELUs, which seem to be divergence-prone
#            # Note that if we are unlucky enough to have diverged within the last 100 timesteps, this may not save us.
#            if losslast100 > 2 * lossbetweensavesprev: 
#                print("We have diverged ! Restoring last savepoint!")
#                net.load_state_dict(torch.load('./torchmodel_'+suffix + '.txt'))
#            else:
            print("Saving local files...")
#            with open('results_'+suffix+'.dat', 'wb') as fo:
#                    pickle.dump(net.w.data.cpu().numpy(), fo)
#                    pickle.dump(net.alpha.data.cpu().numpy(), fo)
#                    pickle.dump(net.eta.data.cpu().numpy(), fo)
#                    pickle.dump(all_losses, fo)
#                    pickle.dump(params, fo)
            #with open('loss_'+suffix+'.txt', 'w') as thefile:
            #    for item in all_losses_objective:
            #            thefile.write("%s\n" % item)
            #with open('lossv_'+suffix+'.txt', 'w') as thefile:
            #    for item in all_losses_v:
            #            thefile.write("%s\n" % item)
            #with open('loss_'+suffix+'.txt', 'w') as thefile:
            #    for item in all_losses_eval:
            #            thefile.write("%s\n" % item)
            #torch.save(net.state_dict(), 'torchmodel_'+suffix+'.txt')
            #print("Saving HDFS files...")
            #if checkHdfs():
            #    print("Transfering to HDFS...")
            #    #transferFileToHdfsDir('results_'+suffix+'.dat', '/ailabs/tmiconi/omniglot/')
            #    transferFileToHdfsDir('loss_'+suffix+'.txt', '/ailabs/tmiconi/gridlab/')
            #    transferFileToHdfsDir('torchmodel_'+suffix+'.txt', '/ailabs/tmiconi/omniglot/')
            #print("Saved!")
#            lossbetweensavesprev = lossbetweensaves
#            lossbetweensaves = 0
#            sys.stdout.flush()
#            sys.stderr.flush()



if __name__ == "__main__":
#defaultParams = {
#    'type' : 'lstm',
#    'seqlen' : 200,
#    'hiddensize': 500,
#    'activ': 'tanh',
#    'steplr': 10e9,  # By default, no change in the learning rate
#    'gamma': .5,  # The annealing factor of learning rate decay for Adam
#    'imagesize': 31,    
#    'nbiter': 30000,  
#    'lr': 1e-4,   
#    'test_every': 10,
#    'save_every': 3000,
#    'rngseed':0
#}
    parser = argparse.ArgumentParser()
    parser.add_argument("--rngseed", type=int, help="random seed", default=0)
    #parser.add_argument("--clamp", type=float, help="maximum (absolute value) gradient for clamping", default=1000000.0)
    parser.add_argument("--bentropy", type=float, help="coefficient for the entropy reward (really Simpson index concentration measure)", default=0.1)
    parser.add_argument("--blossv", type=float, help="coefficient for value prediction loss", default=.1)
    parser.add_argument("--labsize", type=int, help="size of the labyrinth; must be odd", default=7)
    parser.add_argument("--randstart", type=int, help="when hitting reward, should we teleport to random location (1) or center (0)?", default=0)
    parser.add_argument("--rp", type=int, help="whether the reward should be on the periphery", default=0)
    parser.add_argument("--squash", type=int, help="squash reward through signed sqrt (1 or 0)", default=0)
    #parser.add_argument("--nbarms", type=int, help="number of arms", default=2)
    #parser.add_argument("--nbseq", type=int, help="number of sequences between reinitializations of hidden/Hebbian state and position", default=3)
    parser.add_argument("--activ", help="activ function ('tanh' or 'selu')", default='tanh')
    parser.add_argument("--rule", help="learning rule ('hebb' or 'oja')", default='hebb')
    parser.add_argument("--type", help="network type ('lstm' or 'rnn' or 'plastic')", default='rnn')
    parser.add_argument("--gr", type=float, help="gammaR: discounting factor for rewards", default=.99)
    parser.add_argument("--lr", type=float, help="learning rate (Adam optimizer)", default=1e-4)
    parser.add_argument("--eplen", type=int, help="length of episodes", default=100)
    parser.add_argument("--hiddensize", type=int, help="size of the recurrent (hidden) layer", default=100)
    #parser.add_argument("--steplr", type=int, help="duration of each step in the learning rate annealing schedule", default=100000000)
    parser.add_argument("--steplr", type=int, help="duration of each step in the learning rate annealing schedule", default=0)
    parser.add_argument("--gamma", type=float, help="learning rate annealing factor", default=0.3)
    parser.add_argument("--nbiter", type=int, help="number of learning cycles", default=1000000)
    parser.add_argument("--save_every", type=int, help="number of cycles between successive save points", default=200)
    parser.add_argument("--print_every", type=int, help="number of cycles between successive printing of information", default=100)
    #parser.add_argument("--", type=int, help="", default=1e-4)
    args = parser.parse_args(); argvars = vars(args); argdict =  { k : argvars[k] for k in argvars if argvars[k] != None }
    #train()
    train(argdict)

