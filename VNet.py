import caffe
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
import os
import DataManager as DM
import utilities
from os.path import splitext
from multiprocessing import Process, Queue

EPS = 0.0000000001

class VNet(object):
    params=None
    dataManagerTrain=None
    dataManagerTest=None

    def __init__(self,params):
        self.params=params
        caffe.set_device(self.params['ModelParams']['device'])
        caffe.set_mode_gpu()

    def prepareDataThread(self, dataQueue, numpyImages, numpyGT):

        nr_iter = self.params['ModelParams']['numIterations']
        batchsize = self.params['ModelParams']['batchsize']

        keysIMG = numpyImages.keys()

        nr_iter_dataAug = nr_iter*batchsize
        np.random.seed()

        h_patch_size = int(self.params['ModelParams']['patchSize']/2)
        whichImageList = np.random.randint(len(keysIMG), size=int(nr_iter_dataAug/self.params['ModelParams']['nProc']))
        np.random.rand()
        whichCoordinateList_x = np.random.randint(low=h_patch_size + 2,
                                                  high=self.params['DataManagerParams']['VolSize'][0] - h_patch_size -2,
                                                  size=int(nr_iter_dataAug/self.params['ModelParams']['nProc']))
        whichCoordinateList_y = np.random.randint(low=h_patch_size + 2,
                                                  high=self.params['DataManagerParams']['VolSize'][1] - h_patch_size - 2,
                                                  size=int(nr_iter_dataAug / self.params['ModelParams']['nProc']))
        whichCoordinateList_z = np.random.randint(low=h_patch_size + 2,
                                                  high=self.params['DataManagerParams']['VolSize'][2] - h_patch_size - 2,
                                                  size=int(nr_iter_dataAug / self.params['ModelParams']['nProc']))
        whichCoordinateList = np.vstack((whichCoordinateList_x, whichCoordinateList_y, whichCoordinateList_z)).T

        whichDataForMatchingList = np.random.randint(len(keysIMG), size=int(nr_iter_dataAug/self.params['ModelParams']['nProc']))

        assert len(whichCoordinateList) == len(whichDataForMatchingList) == len(whichImageList)

        for whichImage, whichCoordinate, whichDataForMatching in \
                zip(whichImageList, whichCoordinateList, whichDataForMatchingList):
            filename, ext = splitext(keysIMG[whichImage])

            currGtKey = filename + '_segmentation' + ext
            currImgKey = filename + ext

            # data agugumentation through hist matching across different examples...
            ImgKeyMatching = keysIMG[whichDataForMatching]

            img = numpyImages[currImgKey]
            lab = numpyGT[currGtKey]

            img = utilities.hist_match(img, numpyImages[ImgKeyMatching]) #potentially inefficient (slow)
            imgPatch = img[whichCoordinate[0]-h_patch_size-1:whichCoordinate[0]+h_patch_size,
                       whichCoordinate[1] - h_patch_size - 1:whichCoordinate[1] + h_patch_size,
                       whichCoordinate[2] - h_patch_size - 1:whichCoordinate[2] + h_patch_size]

            imgPatchLab = lab[whichCoordinate[0]-h_patch_size-1:whichCoordinate[0]+h_patch_size,
                       whichCoordinate[1] - h_patch_size - 1:whichCoordinate[1] + h_patch_size,
                       whichCoordinate[2] - h_patch_size - 1:whichCoordinate[2] + h_patch_size]


            dataQueue.put(tuple((imgPatch, imgPatchLab)))

    def trainThread(self,dataQueue,solver):

        nr_iter = self.params['ModelParams']['numIterations']
        batchsize = self.params['ModelParams']['batchsize']
        h_p = int(self.params['ModelParams']['patchSize'] / 2)
        batchData = np.zeros((batchsize, 1,
                              self.params['ModelParams']['patchSize'],
                              self.params['ModelParams']['patchSize'],
                              self.params['ModelParams']['patchSize']), dtype=float)
        batchLabel = np.zeros((batchsize, 1), dtype=float)

        train_loss = np.zeros(nr_iter)
        for it in range(nr_iter):
            for i in range(batchsize):
                [patch, label] = dataQueue.get()

                batchData[i, 0, :, :, :] = patch.astype(dtype=np.float32)
                batchLabel[i, 0] = label[h_p, h_p, h_p] > 0.5

            solver.net.blobs['data'].data[...] = batchData.astype(dtype=np.float32)
            solver.net.blobs['label'].data[...] = batchLabel.astype(dtype=np.float32)
            solver.net.blobs['weight'].data[...] = np.ones_like(batchLabel, dtype=np.float32)
            #use only if you do softmax with loss

            solver.step(1)  # this does the training
            train_loss[it] = solver.net.blobs['loss'].data

            if (np.mod(it, 10) == 0):
                plt.clf()
                plt.plot(range(0, it), train_loss[0:it])
                plt.pause(0.00000001)

            matplotlib.pyplot.show()

    def train(self):
        print self.params['ModelParams']['dirTrain']

        #we define here a data manage object
        self.dataManagerTrain = DM.DataManager(self.params['ModelParams']['dirTrain'],
                                               self.params['ModelParams']['dirResult'],
                                               self.params['DataManagerParams'])

        self.dataManagerTrain.loadTrainingData() #loads in sitk format

        howManyImages = len(self.dataManagerTrain.sitkImages)
        howManyGT = len(self.dataManagerTrain.sitkGT)

        assert howManyGT == howManyImages

        print "The dataset has shape: data - " + str(howManyImages) + ". labels - " + str(howManyGT)

        # Write a temporary solver text file because pycaffe is stupid
        if self.params['ModelParams']['solver'] is None:

            with open("solver.prototxt", 'w') as f:
                f.write("net: \"" + self.params['ModelParams']['prototxtTrain'] + "\" \n")
                f.write("base_lr: " + str(self.params['ModelParams']['baseLR']) + " \n")
                f.write("momentum: 0.99 \n")
                f.write("weight_decay: 0.0005 \n")
                f.write("lr_policy: \"step\" \n")
                f.write("stepsize: 20000 \n")
                f.write("gamma: 0.1 \n")
                f.write("display: 1 \n")
                f.write("snapshot: 500 \n")
                f.write("snapshot_prefix: \"" + self.params['ModelParams']['dirSnapshots'] + "\" \n")
                #f.write("test_iter: 3 \n")
                #f.write("test_interval: " + str(test_interval) + "\n")

            f.close()
            solver = caffe.SGDSolver("solver.prototxt")
            os.remove("solver.prototxt")
        else:
            solver = caffe.SGDSolver(self.params['ModelParams']['solver'])

        if (self.params['ModelParams']['snapshot'] > 0):
            solver.restore(self.params['ModelParams']['dirSnapshots'] + "_iter_" + str(
                self.params['ModelParams']['snapshot']) + ".solverstate")

        plt.ion()

        numpyImages = self.dataManagerTrain.getNumpyImages()
        numpyGT = self.dataManagerTrain.getNumpyGT()

        #numpyImages['Case00.mhd']
        #numpy images is a dictionary that you index in this way (with filenames)

        for ii, key in enumerate(numpyImages):
            mean = np.mean(numpyImages[key][numpyImages[key]>0])
            std = np.std(numpyImages[key][numpyImages[key]>0])

            numpyImages[key]-=mean
            numpyImages[key]/=std

        dataQueue = Queue(250) #max 250 patches in queue
        dataPreparation = [None] * self.params['ModelParams']['nProc']

        #thread creation
        for proc in range(0,self.params['ModelParams']['nProc']):
            dataPreparation[proc] = Process(target=self.prepareDataThread, args=(dataQueue, numpyImages, numpyGT))
            dataPreparation[proc].daemon = True
            dataPreparation[proc].start()

        self.trainThread(dataQueue, solver)

    def get_class_and_feature_volume(self, net, volume):
        batchsize = self.params['ModelParams']['batchsize']
        h_patch_size = int(self.params['ModelParams']['patchSize'] / 2)

        #meshgrid xx yy zz
        xx = np.arange(h_patch_size + 1,
                       self.params['DataManagerParams']['VolSize'][0] - h_patch_size -1,
                       step=self.params['ModelParams']['SamplingStep'])
        yy = np.arange(h_patch_size + 1,
                       self.params['DataManagerParams']['VolSize'][1] - h_patch_size - 1,
                       step=self.params['ModelParams']['SamplingStep'])
        zz = np.arange(h_patch_size + 1,
                       self.params['DataManagerParams']['VolSize'][2] - h_patch_size - 1,
                       step=self.params['ModelParams']['SamplingStep'])

        xx, yy, zz = np.meshgrid(xx, yy, zz)

        xx = xx.flatten()
        yy = yy.flatten()
        zz = zz.flatten()

        results_label = np.zeros(xx.shape[0], dtype=int)
        results_probability = np.zeros(xx.shape[0], dtype=np.float32)
        results_feature = np.zeros((xx.shape[0], int(self.params['ModelParams']['featLength'])), dtype=np.float32)

        for i in range(int(np.ceil(xx.shape[0] / batchsize))):
            curr_xx = xx[i * batchsize:(i + 1) * batchsize]
            curr_yy = yy[i * batchsize:(i + 1) * batchsize]
            curr_zz = zz[i * batchsize:(i + 1) * batchsize]

            imgPatches = np.zeros((self.params['ModelParams']['batchsize'], 1,
                                   self.params['ModelParams']['patchSize'],
                                   self.params['ModelParams']['patchSize'],
                                   self.params['ModelParams']['patchSize']), dtype=np.float32)

            for x_, y_, z_, k in zip(curr_xx, curr_yy, curr_zz, range(len(curr_xx))):
                imgPatches[k, 0] = volume[x_ - h_patch_size - 1:x_ + h_patch_size,
                                          y_ - h_patch_size - 1:y_ + h_patch_size,
                                          z_ - h_patch_size - 1:z_ + h_patch_size]
            net.blobs['data'].data[...] = imgPatches

            out = net.forward()
            print out.keys()
            l = np.argmax(out["pred"], axis=1)
            p = out["pred"][:, l]
            f = out["fc2_out"]


            results_label[i * batchsize:(i + 1) * batchsize] = l
            results_feature[i * batchsize:(i + 1) * batchsize] = f
            results_probability[i * batchsize:(i + 1) * batchsize] = p

        return int(results_label), results_probability, results_feature, (xx, yy, zz)

    def cast_votes_and_segment(self, results_label, results_probability, results_feature, coords):
        votemap = np.zeros((self.params['DataManagerParams']['VolSize'][0],
                  self.params['DataManagerParams']['VolSize'][1],
                  self.params['DataManagerParams']['VolSize'][2]), dtype=np.float32)

        segmentation = np.zeros((self.params['DataManagerParams']['VolSize'][0],
                            self.params['DataManagerParams']['VolSize'][1],
                            self.params['DataManagerParams']['VolSize'][2]), dtype=np.float32)

        denominator = np.zeros((self.params['DataManagerParams']['VolSize'][0],
                                 self.params['DataManagerParams']['VolSize'][1],
                                 self.params['DataManagerParams']['VolSize'][2]), dtype=np.float32)

        results_feature = results_feature[results_label > 0]
        coords = coords[results_label > 0]

        # todo: Knn search via flann or similar
        neighbors_idx, votes, seg_patch_coords, seg_patch_vol, distance = self.k_nn_search(results_feature)

        dst_votes = np.tile(coords, (0, self.params['DataManagerParams']['numNeighs'])) + votes

        for i in range(0, self.params['DataManagerParams']['numNeighs']):
            curr_votes = dst_votes[:, i * 3:(i + 1) * 3]
            votemap[curr_votes] += 1.0 / (distance[i] + 1.0)

        xc, yc, zc = np.argmax(votemap)

        h_seg_patch_size = int(self.params['ModelParams']['SegPatchSize'] / 2)

        for i in range(0, self.params['DataManagerParams']['numNeighs']):
            curr_votes = dst_votes[:, i * 3:(i + 1) * 3]
            reject_votes = abs(curr_votes - np.asarray([xc, yc, zc])) < self.params['DataManagerParams']['centrtol']
            w = 1.0 / (distance[i] + 1.0)

            curr_dst_coords = coords[reject_votes]
            curr_seg_patch_coords = seg_patch_coords[reject_votes]
            curr_seg_patch_vol = seg_patch_vol[reject_votes]
            curr_weight = w[reject_votes]

            patches = self.retrieve_seg_patches(curr_seg_patch_coords, curr_seg_patch_vol)

            #apply patches in appropriate places

            for p, c, w in zip(patches, curr_dst_coords, curr_weight):
                segmentation[c[0] - h_seg_patch_size - 1:c[0] + h_seg_patch_size,
                             c[1] - h_seg_patch_size - 1:c[1] + h_seg_patch_size,
                             c[2] - h_seg_patch_size - 1:c[2] + h_seg_patch_size] += p * w

                denominator[c[0] - h_seg_patch_size - 1:c[0] + h_seg_patch_size,
                            c[1] - h_seg_patch_size - 1:c[1] + h_seg_patch_size,
                            c[2] - h_seg_patch_size - 1:c[2] + h_seg_patch_size] += w

        segmentation /= (denominator+EPS)

        return votemap, segmentation

    def create_database(self):
        #todo save pkl database file of features and coordinates
        print('todo')

    def load_database(self):
        #todo load PKL database file
        print('todo')

    def test(self):
        assert self.params['DataManagerParams']['VolSize'][0] == \
               self.params['DataManagerParams']['VolSize'][1] == \
               self.params['DataManagerParams']['VolSize'][2]

        self.dataManagerTest = DM.DataManager(self.params['ModelParams']['dirTest'], self.params['ModelParams']['dirResult'], self.params['DataManagerParams'])
        self.dataManagerTest.loadTestData()

        net = caffe.Net(self.params['ModelParams']['prototxtTest'],
                        os.path.join(self.params['ModelParams']['dirSnapshots'],"_iter_" + str(self.params['ModelParams']['snapshot']) + ".caffemodel"),
                        caffe.TEST)

        numpyImages = self.dataManagerTest.getNumpyImages()
        for key in numpyImages:
            mean = np.mean(numpyImages[key][numpyImages[key]>0])
            std = np.std(numpyImages[key][numpyImages[key]>0])

            numpyImages[key] -= mean
            numpyImages[key] /= std

        results = dict()

        for key in numpyImages:
            results_label, results_probability, results_feature, coords = self.get_class_and_feature_volume(net, numpyImages[key])

            votemap, segmentation = self.cast_votes_and_segment(results_label, results_probability, results_feature, coords)

            print(segmentation.shape)
            print('done {}'.format(key))
            results[key] = segmentation

            print("{} foreground voxels".format(np.sum(results[key]>0.5)))

            self.dataManagerTest.writeResultsFromNumpyLabel(results[key], key)
